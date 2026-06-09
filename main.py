"""
SAP AI Core → OpenAI Compatible Proxy
Converts OpenAI-spec requests to SAP AI Core format with automatic OAuth2 token management.
"""

import time
import httpx
import asyncio
import logging
import json
import os
import datetime
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic_settings import BaseSettings

# ── Config ──────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    ai_core_client_id: str = ""
    ai_core_client_secret: str = ""
    ai_core_base_url: str = ""
    ai_core_auth_url: str = ""
    ai_core_resource_group: str = "default"
    ai_core_deployment_id: str = ""

    proxy_port: int = 8000
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

def load_settings() -> Settings:
    overrides = {}
    
    if os.path.exists("sap_key.json"):
        try:
            with open("sap_key.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if "clientid" in data: overrides["ai_core_client_id"] = data["clientid"]
                if "clientsecret" in data: overrides["ai_core_client_secret"] = data["clientsecret"]
                if "serviceurls" in data and "AI_API_URL" in data["serviceurls"]:
                    overrides["ai_core_base_url"] = data["serviceurls"]["AI_API_URL"]
                if "url" in data: overrides["ai_core_auth_url"] = data["url"]
                if "deployment_id" in data: overrides["ai_core_deployment_id"] = data["deployment_id"]
                if "resource_group" in data: overrides["ai_core_resource_group"] = data["resource_group"]
        except Exception as e:
            logging.warning(f"Failed to parse sap_key.json: {e}")

    return Settings(**overrides)

settings = load_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("sap-proxy")

def load_allowed_keys() -> Dict[str, Dict[str, Any]]:
    keys_config = {}
    if os.path.exists("allowed_keys.json"):
        try:
            with open("allowed_keys.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for k in data:
                        keys_config[k] = {}
                elif isinstance(data, dict):
                    # Check if it's the old format {"keys": ["k1", "k2"]}
                    if "keys" in data and isinstance(data["keys"], list):
                        for k in data["keys"]:
                            keys_config[k] = {}
                    else:
                        # Assume it's dict mapping: {"key1": {"user_id": "...", ...}, ...}
                        for k, v in data.items():
                            if isinstance(v, dict):
                                keys_config[k] = v
                            else:
                                keys_config[k] = {}
        except Exception as e:
            logger.warning(f"Failed to parse allowed_keys.json: {e}")
    
    # Also support comma-separated env var for simple keys
    env_keys = os.environ.get("ALLOWED_API_KEYS", "")
    if env_keys:
        for k in env_keys.split(","):
            k = k.strip()
            if k and k not in keys_config:
                keys_config[k] = {}
                
    return keys_config

allowed_keys = load_allowed_keys()

# ── Usage Logging ────────────────────────────────────────────────────────────
async def log_usage(user_id: str, deployment_id: str, usage: dict):
    if not usage:
        return
    log_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user_id": user_id,
        "deployment_id": deployment_id,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0)
    }
    try:
        with open("usage.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write usage log: {e}")

# ── Token Cache ──────────────────────────────────────────────────────────────

class TokenCache:
    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            await self._refresh()
            return self._token

    async def _refresh(self):
        logger.info("Refreshing SAP AI Core OAuth token...")
        if not settings.ai_core_auth_url or not settings.ai_core_client_id:
            raise ValueError("Missing SAP authentication configuration (client_id, auth_url).")
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.ai_core_auth_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.ai_core_client_id,
                    "client_secret": settings.ai_core_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._expires_at = time.time() + data.get("expires_in", 3600)
            logger.info(f"Token refreshed. Expires in {data.get('expires_in', 3600)}s")

token_cache = TokenCache()

# ── FastAPI App ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ai_core_client_id and settings.ai_core_auth_url:
        try:
            await token_cache.get()
            logger.info("SAP AI Core proxy ready.")
        except Exception as e:
            logger.warning(f"Could not pre-warm token: {e}")
    else:
        logger.warning("SAP credentials missing. Token not pre-warmed.")
    yield

app = FastAPI(title="SAP AI Core Proxy", lifespan=lifespan)

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    request.state.key_config = {}
    
    if request.url.path == "/health":
        return await call_next(request)
        
    if allowed_keys:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Missing or invalid Authorization header"})
        
        token = auth_header.split(" ")[1]
        if token not in allowed_keys:
            logger.warning(f"Unauthorized access attempt with key: {token[:4]}...")
            return JSONResponse(status_code=401, content={"error": "Unauthorized API Key"})
            
        request.state.key_config = allowed_keys[token]
        # set default user_id if not present
        if "user_id" not in request.state.key_config:
            request.state.key_config["user_id"] = f"key-{token[:4]}***"
            
    return await call_next(request)

# ── Proxy Endpoint ────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    token = await token_cache.get()
    key_config = getattr(request.state, "key_config", {})

    # 우선순위: Header > API Key 설정 > Request Body(model) > 환경변수(.env/sap_key.json)
    req_deployment_id = (
        request.headers.get("x-sap-deployment-id") or 
        key_config.get("deployment_id") or 
        body.get("model") or 
        settings.ai_core_deployment_id
    )
    
    req_resource_group = (
        request.headers.get("x-sap-resource-group") or 
        key_config.get("resource_group") or 
        settings.ai_core_resource_group
    )

    if not req_deployment_id:
        raise HTTPException(
            status_code=400, 
            detail="Missing deployment ID. Pass it via 'model' field, API Key config, or x-sap-deployment-id header."
        )

    target_url = (
        f"{settings.ai_core_base_url.rstrip('/')}"
        f"/v2/inference/deployments/{req_deployment_id}"
        f"/chat/completions"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": req_resource_group,
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
                            # Try to extract usage from SSE chunk
                            if line.startswith("data: ") and not line.endswith("[DONE]"):
                                try:
                                    chunk_data = json.loads(line[6:])
                                    if "usage" in chunk_data and chunk_data["usage"]:
                                        usage_data = chunk_data["usage"]
                                except:
                                    pass
                # Log usage after stream is fully consumed
                if usage_data:
                    await log_usage(user_id, req_deployment_id, usage_data)

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            resp = await client.post(target_url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error(f"SAP error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
                
            resp_json = resp.json()
            if "usage" in resp_json:
                await log_usage(user_id, req_deployment_id, resp_json["usage"])
                
            return JSONResponse(content=resp_json)

# ── Models endpoint ────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models(request: Request):
    key_config = getattr(request.state, "key_config", {})
    req_deployment_id = (
        request.headers.get("x-sap-deployment-id") or 
        key_config.get("deployment_id") or 
        settings.ai_core_deployment_id
    )
    return {
        "object": "list",
        "data": [
            {
                "id": req_deployment_id or "unknown-deployment",
                "object": "model",
                "created": 0,
                "owned_by": "sap-ai-core",
            }
        ],
    }

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok", 
        "deployment_id": settings.ai_core_deployment_id,
        "auth_enabled": len(allowed_keys) > 0
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.proxy_port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
