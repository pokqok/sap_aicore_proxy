"""
Proxy router — OpenAI-compatible endpoints that forward to SAP AI Core Orchestration.

Transforms OpenAI-format requests into SAP Orchestration format,
and converts Orchestration responses back to OpenAI format.
"""

import json
import logging
import time
import uuid

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from usage import log_usage

logger = logging.getLogger("sap-proxy")

router = APIRouter(prefix="/v1")


def _resolve_routing(request: Request, body: dict = None):
    """Determine deployment_id and resource_group from priority chain."""
    from main import settings  # deferred to avoid circular import

    key_config = getattr(request.state, "key_config", {})

    deployment_id = (
        request.headers.get("x-sap-deployment-id")
        or key_config.get("deployment_id")
        or settings.ai_core_deployment_id
    )

    resource_group = (
        request.headers.get("x-sap-resource-group")
        or key_config.get("resource_group")
        or settings.ai_core_resource_group
    )

    return deployment_id, resource_group


def _build_orchestration_body(body: dict) -> dict:
    """
    Convert an OpenAI-format request body into SAP Orchestration format.

    OpenAI format:
      {"model": "gpt-4o", "messages": [...], "temperature": 0.7, "max_tokens": 100, "stream": true}

    Orchestration format:
      {
        "orchestration_config": {
          "modules": {
            "llm_module_config": {
              "model_name": "gpt-4o",
              "model_params": {"temperature": 0.7, "max_tokens": 100}
            }
          }
        },
        "messages_history": [...],
        "stream": true
      }
    """
    model_name = body.get("model", "gpt-4o")

    # Extract model params from the OpenAI body
    model_params = {}
    param_keys = ["temperature", "max_tokens", "top_p", "frequency_penalty",
                  "presence_penalty", "stop", "n"]
    for key in param_keys:
        if key in body:
            model_params[key] = body[key]


    # Build orchestration request
    messages = body.get("messages", [])

    # SAP Orchestration requires non-empty template.
    # Use the last user message as template, rest goes to messages_history.
    template_messages = []
    history_messages = []

    if messages:
        # Put the last message into template, everything else into history
        template_messages = [messages[-1]]
        history_messages = messages[:-1]
    else:
        template_messages = [{"role": "user", "content": "{{?input}}"}]

    orch_body = {
        "orchestration_config": {
            "module_configurations": {
                "llm_module_config": {
                    "model_name": model_name,
                    "model_params": model_params,
                },
                "templating_module_config": {
                    "template": template_messages
                }
            }
        },
    }

    if body.get("stream"):
        orch_body["orchestration_config"]["stream"] = True

    # Only include messages_history if there are prior messages
    if history_messages:
        orch_body["messages_history"] = history_messages

    return orch_body


def _orch_response_to_openai(orch_json: dict, model_name: str) -> dict:
    """
    Convert SAP Orchestration response to OpenAI-compatible format.

    Orchestration may return the result in 'orchestration_result' or
    directly in an OpenAI-like structure. Handle both gracefully.
    """
    # If the response already looks like OpenAI format, return as-is
    if "choices" in orch_json and "object" in orch_json:
        return orch_json

    # Extract from orchestration wrapper
    result = orch_json.get("orchestration_result", orch_json)

    # If orchestration_result itself is OpenAI-like
    if "choices" in result:
        return result

    # Build OpenAI response from scratch if needed
    return {
        "id": orch_json.get("request_id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": result.get("choices", []),
        "usage": result.get("usage", {}),
    }


def _orch_stream_chunk_to_openai(line: str, model_name: str) -> str | None:
    """
    Convert a single SSE line from Orchestration stream to OpenAI SSE format.
    Returns the converted SSE line string, or None if it should be skipped.
    """
    # If line doesn't have SSE 'data: ' prefix but looks like JSON, treat it as data
    if not line.startswith("data: "):
        if line.startswith("{"):
            data_str = line.strip()
        else:
            return None  # skip non-data, non-JSON lines (e.g. "event:", "id:" lines)
    else:
        data_str = line[6:].strip()

    # Pass through [DONE]
    if data_str == "[DONE]":
        return "data: [DONE]"

    try:
        chunk = json.loads(data_str)
    except json.JSONDecodeError:
        return line  # pass through unparseable lines

    # Extract from orchestration wrapper if present
    result = chunk.get("orchestration_result", chunk)

    # Resolve choices from result (or top-level chunk)
    choices = result.get("choices", chunk.get("choices", []))

    # Normalize choices: convert message → delta for streaming format
    normalized_choices = []
    for choice in choices:
        c = dict(choice)
        # If streaming sent 'message' instead of 'delta', convert it
        if "message" in c and "delta" not in c:
            c["delta"] = {"role": c["message"].get("role", "assistant"),
                          "content": c["message"].get("content", "")}
            del c["message"]
        elif "delta" not in c:
            c["delta"] = {}
        normalized_choices.append(c)

    req_id = (result.get("id")
              or chunk.get("request_id")
              or f"chatcmpl-{uuid.uuid4().hex[:12]}")

    openai_chunk = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": result.get("created", int(time.time())),
        "model": result.get("model", model_name),
        "choices": normalized_choices,
    }
    if "usage" in result and result["usage"]:
        openai_chunk["usage"] = result["usage"]

    return f"data: {json.dumps(openai_chunk)}"


@router.post("/chat/completions")
async def chat_completions(request: Request):
    import httpx
    from main import settings, token_cache

    body = await request.json()
    token = await token_cache.get()
    key_config = getattr(request.state, "key_config", {})

    deployment_id, resource_group = _resolve_routing(request, body)

    if not deployment_id:
        raise HTTPException(
            status_code=400,
            detail="Missing deployment ID. Set it via API Key config or x-sap-deployment-id header.",
        )

    # Orchestration uses /completion (not /chat/completions)
    target_url = (
        f"{settings.ai_core_base_url.rstrip('/')}"
        f"/v2/inference/deployments/{deployment_id}"
        f"/completion"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": resource_group,
        "Content-Type": "application/json",
    }

    # Resolve model name: key_config > request body > default
    model_name = key_config.get("model_name") or body.get("model", "gpt-4o")
    is_stream = body.get("stream", False)
    user_id = key_config.get("user_id", "anonymous")

    # Override model in body so _build_orchestration_body uses the resolved name
    body["model"] = model_name

    # Convert OpenAI body → Orchestration body
    orch_body = _build_orchestration_body(body)

    logger.info(f"[{user_id}] → Orchestration deployment={deployment_id}, model={model_name}, stream={is_stream}")

    if is_stream:
        async def stream_generator():
            usage_data = None
            async with httpx.AsyncClient(timeout=120) as stream_client:
                async with stream_client.stream("POST", target_url, json=orch_body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        error_text = error_body.decode()
                        logger.error(f"SAP error {resp.status_code}: {error_body}")

                        if resp.status_code == 400 and "Streaming is not supported for this model" in error_text:
                            async with httpx.AsyncClient(timeout=120) as fallback_client:
                                fallback_body = dict(orch_body)
                                fallback_orch_config = dict(fallback_body.get("orchestration_config", {}))
                                fallback_orch_config.pop("stream", None)
                                fallback_body["orchestration_config"] = fallback_orch_config

                                fallback_resp = await fallback_client.post(target_url, json=fallback_body, headers=headers)
                                if fallback_resp.status_code != 200:
                                    logger.error(f"SAP fallback error {fallback_resp.status_code}: {fallback_resp.text}")
                                    yield f"data: {fallback_resp.text}\n\n"
                                    return

                                resp_json = fallback_resp.json()
                                openai_resp = _orch_response_to_openai(resp_json, model_name)
                                choices = openai_resp.get("choices", [])
                                usage = openai_resp.get("usage")
                                req_id = openai_resp.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}")

                                if choices:
                                    content = choices[0].get("message", {}).get("content", "")
                                    chunk = {
                                        "id": req_id,
                                        "object": "chat.completion.chunk",
                                        "created": openai_resp.get("created", int(time.time())),
                                        "model": model_name,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"role": "assistant", "content": content},
                                            "finish_reason": None,
                                        }],
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"

                                    final_chunk = {
                                        "id": req_id,
                                        "object": "chat.completion.chunk",
                                        "created": openai_resp.get("created", int(time.time())),
                                        "model": model_name,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": choices[0].get("finish_reason", "stop"),
                                        }],
                                    }
                                    if usage:
                                        final_chunk["usage"] = usage
                                        usage_data = usage
                                    yield f"data: {json.dumps(final_chunk)}\n\n"

                                yield "data: [DONE]\n\n"
                                return

                        yield f"data: {error_text}\n\n"
                        return
                    sent_done = False
                    sent_finish_reason = False
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        logger.info(f"[stream raw] {line[:200]}")
                        converted = _orch_stream_chunk_to_openai(line, model_name)
                        if converted:
                            yield converted + "\n\n"
                            if converted.strip() == "data: [DONE]":
                                sent_done = True
                            elif converted.startswith("data: "):
                                try:
                                    chunk_data = json.loads(converted[6:])
                                    if "usage" in chunk_data and chunk_data["usage"]:
                                        usage_data = chunk_data["usage"]
                                    for choice in chunk_data.get("choices", []):
                                        if choice.get("finish_reason") is not None:
                                            sent_finish_reason = True
                                except Exception:
                                    pass

                    if not sent_finish_reason:
                        final_chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }],
                        }
                        if usage_data:
                            final_chunk["usage"] = usage_data
                        yield f"data: {json.dumps(final_chunk)}\n\n"

                    if not sent_done:
                        yield "data: [DONE]\n\n"

            if usage_data:
                await log_usage(user_id, deployment_id, usage_data)

        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(target_url, json=orch_body, headers=headers)
            if resp.status_code != 200:
                logger.error(f"SAP error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            resp_json = resp.json()
            openai_resp = _orch_response_to_openai(resp_json, model_name)

            if "usage" in openai_resp:
                await log_usage(user_id, deployment_id, openai_resp["usage"])

            return JSONResponse(content=openai_resp)


@router.get("/models")
async def list_models(request: Request):
    key_config = getattr(request.state, "key_config", {})
    deployment_id, _ = _resolve_routing(request)
    model_name = key_config.get("model_name") or "gpt-4o"

    return {
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": 0,
                "owned_by": "sap-ai-core",
            },
            {
                "id": deployment_id or "orchestration",
                "object": "model",
                "created": 0,
                "owned_by": "sap-ai-core-deployment",
            },
        ],
    }
