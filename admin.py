"""
Admin router — Dashboard UI and management API endpoints.
"""

import os
import secrets
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from config import save_allowed_keys
from usage import read_usage_logs, get_usage_stats

logger = logging.getLogger("sap-proxy")

router = APIRouter(prefix="/admin")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _verify_admin(request: Request):
    """Verify admin password from Authorization header."""
    from main import settings
    if not settings.admin_password:
        return  # no password set, allow access
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid admin password")


# ── Admin UI Page ────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def admin_page():
    html_path = os.path.join(STATIC_DIR, "admin.html")
    if not os.path.exists(html_path):
        return HTMLResponse("<h1>Admin UI not found</h1><p>static/admin.html is missing.</p>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Admin API: Stats ─────────────────────────────────────────────────────────

@router.get("/api/stats")
async def admin_stats(request: Request):
    _verify_admin(request)
    from main import settings, allowed_keys

    usage_stats = get_usage_stats()
    return {
        "total_keys": len(allowed_keys),
        "server_status": "ok",
        "deployment_id": settings.ai_core_deployment_id,
        **usage_stats,
    }


# ── Admin API: Key Management ────────────────────────────────────────────────

@router.get("/api/keys")
async def admin_list_keys(request: Request):
    _verify_admin(request)
    from main import allowed_keys
    return {"keys": allowed_keys}


@router.post("/api/keys")
async def admin_add_key(request: Request):
    _verify_admin(request)
    from main import allowed_keys

    body = await request.json()
    key = body.get("key", "").strip()
    if not key:
        key = f"sk-{secrets.token_hex(16)}"

    config = body.get("config", {})
    if not config.get("user_id"):
        config["user_id"] = f"user-{key[:6]}"

    if key in allowed_keys:
        raise HTTPException(status_code=409, detail="Key already exists")

    allowed_keys[key] = config
    save_allowed_keys(allowed_keys)
    return {"key": key, "config": config}


@router.put("/api/keys/{key}")
async def admin_update_key(key: str, request: Request):
    _verify_admin(request)
    from main import allowed_keys

    if key not in allowed_keys:
        raise HTTPException(status_code=404, detail="Key not found")

    body = await request.json()
    config = body.get("config", {})
    allowed_keys[key] = config
    save_allowed_keys(allowed_keys)
    return {"key": key, "config": config}


@router.delete("/api/keys/{key}")
async def admin_delete_key(key: str, request: Request):
    _verify_admin(request)
    from main import allowed_keys

    if key not in allowed_keys:
        raise HTTPException(status_code=404, detail="Key not found")

    del allowed_keys[key]
    save_allowed_keys(allowed_keys)
    return {"deleted": key}


# ── Admin API: Usage Logs ─────────────────────────────────────────────────────

@router.get("/api/usage")
async def admin_usage(request: Request, user_id: str = None, date: str = None, limit: int = 500):
    _verify_admin(request)
    return read_usage_logs(user_id=user_id, date=date, limit=limit)
