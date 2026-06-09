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
from typing import Optional
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
    
    # 1. Load from sap_key.json if exists
    if os.path.exists("sap_key.json"):
        try:
            with open("sap_key.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if "clientid" in data:
                    overrides["ai_core_client_id"] = data["clientid"]
                if "clientsecret" in data:
                    overrides["ai_core_client_secret"] = data["clientsecret"]
                if "serviceurls" in data and "AI_API_URL" in data["serviceurls"]:
                    overrides["ai_core_base_url"] = data["serviceurls"]["AI_API_URL"]
                if "url" in data:
                    overrides["ai_core_auth_url"] = data["url"]
                if "deployment_id" in data:
                    overrides["ai_core_deployment_id"] = data["deployment_id"]
                if "resource_group" in data:
                    overrides["ai_core_resource_group"] = data["resource_group"]
        except Exception as e:
            logging.warning(f"Failed to parse sap_key.json: {e}")

    # Settings initializes with .env vars first, then applies kwargs overrides.
    # We pass overrides to ensure JSON takes precedence over empty defaults.
    # To properly merge .env and JSON, we initialize Settings() normally, 
    # then apply JSON overrides if the .env didn't already provide them, 
    # but the request was "JSON overrides .env". So kwargs is correct.
    s = Settings(**overrides)
    return s

settings = load_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("sap-proxy")

def load_allowed_keys() -> list[str]:
    keys = []
    if os.path.exists("allowed_keys.json"):
        try:
            with open("allowed_keys.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    keys.extend(data)
                elif isinstance(data, dict) and "keys" in data:
                    keys.extend(data["keys"])
        except Exception as e:
            logger.warning(f"Failed to parse allowed_keys.json: {e}")
    
    # Also support comma-separated env var
    env_keys = os.environ.get("ALLOWED_API_KEYS", "")
    if env_keys:
        keys.extend([k.strip() for k in env_keys.split(",") if k.strip()])
        
    return keys

allowed_keys = load_allowed_keys()

# ── Token Cache ──────────────────────────────────────────────────────────────

class TokenCache:
    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:  # 60s buffer
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
    # Pre-warm token on startup
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
            
    return await call_next(request)

# ── Proxy Endpoint ────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    token = await token_cache.get()

    # 동적 헤더 처리 (오버라이드)
    req_deployment_id = request.headers.get("x-sap-deployment-id", settings.ai_core_deployment_id)
    req_resource_group = request.headers.get("x-sap-resource-group", settings.ai_core_resource_group)

    if not req_deployment_id:
        raise HTTPException(
            status_code=400, 
            detail="Missing deployment ID. Set AI_CORE_DEPLOYMENT_ID in config or pass 'x-sap-deployment-id' header."
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

    # SAP AI Core ignores the model field but some tools require it — strip to avoid confusion
    body.pop("model", None)

    is_stream = body.get("stream", False)

    async with httpx.AsyncClient(timeout=120) as client:
        if is_stream:
            async def stream_generator():
                async with client.stream("POST", target_url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        logger.error(f"SAP error {resp.status_code}: {error_body}")
                        yield f"data: {error_body.decode()}\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            resp = await client.post(target_url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.error(f"SAP error {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return JSONResponse(content=resp.json())


# ── Models endpoint (fake, for tool compatibility) ────────────────────────────

@app.get("/v1/models")
async def list_models(request: Request):
    req_deployment_id = request.headers.get("x-sap-deployment-id", settings.ai_core_deployment_id)
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
    # BAS 환경에서는 PORT 환경변수를 읽어와야 할 수 있으므로 안전하게 처리
    port = int(os.environ.get("PORT", settings.proxy_port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
