"""
SAP AI Core → OpenAI Compatible Proxy
Entry point: app creation, middleware, lifespan, and module wiring.
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import load_settings, load_allowed_keys
from auth import TokenCache

# ── Initialize ───────────────────────────────────────────────────────────────

settings = load_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("sap-proxy")

allowed_keys = load_allowed_keys()
token_cache = TokenCache(settings)

# ── App ──────────────────────────────────────────────────────────────────────

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

# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    request.state.key_config = {}
    path = request.url.path

    # Skip auth for health and admin pages
    if path == "/health" or path.startswith("/admin"):
        return await call_next(request)

    if allowed_keys:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Missing or invalid Authorization header"})

        token = auth_header.split(" ", 1)[1]
        if token not in allowed_keys:
            logger.warning(f"Unauthorized access attempt with key: {token[:4]}...")
            return JSONResponse(status_code=401, content={"error": "Unauthorized API Key"})

        request.state.key_config = allowed_keys[token]
        if "user_id" not in request.state.key_config:
            request.state.key_config["user_id"] = f"key-{token[:4]}***"

    return await call_next(request)

# ── Register Routers ─────────────────────────────────────────────────────────

from proxy import router as proxy_router
from admin import router as admin_router

app.include_router(proxy_router)
app.include_router(admin_router)

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "deployment_id": settings.ai_core_deployment_id,
        "auth_enabled": len(allowed_keys) > 0,
    }

# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.proxy_port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
