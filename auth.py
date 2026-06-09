"""
OAuth2 token management for SAP AI Core (XSUAA).
"""

import time
import httpx
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("sap-proxy")


class TokenCache:
    def __init__(self, settings):
        self._settings = settings
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
        s = self._settings
        logger.info("Refreshing SAP AI Core OAuth token...")
        if not s.ai_core_auth_url or not s.ai_core_client_id:
            raise ValueError("Missing SAP authentication configuration (client_id, auth_url).")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{s.ai_core_auth_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.ai_core_client_id,
                    "client_secret": s.ai_core_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._expires_at = time.time() + data.get("expires_in", 3600)
            logger.info(f"Token refreshed. Expires in {data.get('expires_in', 3600)}s")
