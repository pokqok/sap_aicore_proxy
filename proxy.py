"""
Proxy router — OpenAI-compatible endpoints that forward to SAP AI Core.
"""

import json
import logging

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
        or (body.get("model") if body else None)
        or settings.ai_core_deployment_id
    )

    resource_group = (
        request.headers.get("x-sap-resource-group")
        or key_config.get("resource_group")
        or settings.ai_core_resource_group
    )

    return deployment_id, resource_group


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
            detail="Missing deployment ID. Set it via API Key config, 'model' field, or x-sap-deployment-id header.",
        )

    query_str = request.url.query
    if "api-version" not in query_str:
        query_str = query_str + "&api-version=2024-02-01" if query_str else "api-version=2024-02-01"

    target_url = (
        f"{settings.ai_core_base_url.rstrip('/')}"
        f"/v2/inference/deployments/{deployment_id}"
        f"/chat/completions?{query_str}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": resource_group,
        "Content-Type": "application/json",
    }

    body.pop("model", None)
    is_stream = body.get("stream", False)
    user_id = key_config.get("user_id", "anonymous")

    async with httpx.AsyncClient(timeout=120) as client:
        if is_stream:
            async def stream_generator():
                usage_data = None
                async with client.stream("POST", target_url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        logger.error(f"SAP error {resp.status_code}: {error_body}")
                        yield f"data: {error_body.decode()}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
                            if line.startswith("data: ") and not line.endswith("[DONE]"):
                                try:
                                    chunk_data = json.loads(line[6:])
                                    if "usage" in chunk_data and chunk_data["usage"]:
                                        usage_data = chunk_data["usage"]
                                except Exception:
                                    pass
                if usage_data:
                    await log_usage(user_id, deployment_id, usage_data)

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            resp = await client.post(target_url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error(f"SAP error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            resp_json = resp.json()
            if "usage" in resp_json:
                await log_usage(user_id, deployment_id, resp_json["usage"])

            return JSONResponse(content=resp_json)


@router.get("/models")
async def list_models(request: Request):
    from main import settings

    deployment_id, _ = _resolve_routing(request)
    return {
        "object": "list",
        "data": [
            {
                "id": deployment_id or "unknown-deployment",
                "object": "model",
                "created": 0,
                "owned_by": "sap-ai-core",
            }
        ],
    }
