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

    # Only include messages_history if there are prior messages
    if history_messages:
        orch_body["messages_history"] = history_messages

    # Pass through stream flag
    if body.get("stream"):
        orch_body["stream"] = True

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
    if not line.startswith("data: "):
        return line

    data_str = line[6:].strip()

    # Pass through [DONE]
    if data_str == "[DONE]":
        return "data: [DONE]"

    try:
        chunk = json.loads(data_str)
    except json.JSONDecodeError:
        return line  # pass through unparseable lines

    # If it already looks like an OpenAI chunk, pass through
    if "object" in chunk and chunk.get("object", "").startswith("chat.completion"):
        return line

    # Extract from orchestration wrapper
    result = chunk.get("orchestration_result", chunk)
    if "choices" in result:
        openai_chunk = result
    else:
        openai_chunk = {
            "id": chunk.get("request_id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": result.get("choices", []),
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

    model_name = body.get("model", "gpt-4o")
    is_stream = body.get("stream", False)
    user_id = key_config.get("user_id", "anonymous")

    # Convert OpenAI body → Orchestration body
    orch_body = _build_orchestration_body(body)

    logger.info(f"[{user_id}] → Orchestration deployment={deployment_id}, model={model_name}, stream={is_stream}")

    async with httpx.AsyncClient(timeout=120) as client:
        if is_stream:
            async def stream_generator():
                usage_data = None
                async with client.stream("POST", target_url, json=orch_body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        logger.error(f"SAP error {resp.status_code}: {error_body}")
                        yield f"data: {error_body.decode()}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        converted = _orch_stream_chunk_to_openai(line, model_name)
                        if converted:
                            yield converted + "\n\n"
                            # Try to extract usage from the chunk
                            if converted.startswith("data: ") and not converted.endswith("[DONE]"):
                                try:
                                    chunk_data = json.loads(converted[6:])
                                    if "usage" in chunk_data and chunk_data["usage"]:
                                        usage_data = chunk_data["usage"]
                                except Exception:
                                    pass
                if usage_data:
                    await log_usage(user_id, deployment_id, usage_data)

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
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
    from main import settings

    deployment_id, _ = _resolve_routing(request)
    return {
        "object": "list",
        "data": [
            {
                "id": deployment_id or "orchestration",
                "object": "model",
                "created": 0,
                "owned_by": "sap-ai-core",
            }
        ],
    }
