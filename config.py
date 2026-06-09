"""
Configuration management for the SAP AI Core proxy.
Loads settings from .env, sap_key.json, and allowed_keys.json.
"""

import os
import json
import logging
from typing import Dict, Any, List

from pydantic_settings import BaseSettings

logger = logging.getLogger("sap-proxy")


class Settings(BaseSettings):
    ai_core_client_id: str = ""
    ai_core_client_secret: str = ""
    ai_core_base_url: str = ""
    ai_core_auth_url: str = ""
    ai_core_resource_group: str = "default"
    ai_core_deployment_id: str = ""

    proxy_port: int = 8000
    log_level: str = "INFO"
    admin_password: str = ""
    available_models: str = (
        "amazon--nova-premier,amazon--nova-pro,amazon--nova-lite,amazon--nova-micro,"
        "gpt-5,gpt-5.4,gpt-5.4-nano,gpt-5-mini,gpt-5-nano,gpt-4o,gpt-4o-mini,"
        "gpt-4.1,gpt-4.1-mini,gpt-4.1-nano,o1,o3,o3-mini,o4-mini,gemini-2.5-pro,"
        "gemini-2.5-flash,anthropic--claude-3-haiku,anthropic--claude-4-sonnet,"
        "anthropic--claude-4.5-sonnet,anthropic--claude-4.5-haiku,"
        "anthropic--claude-4.6-sonnet,anthropic--claude-4.6-opus,"
        "anthropic--claude-4.7-opus,mistralai--mistral-large-instruct,"
        "mistralai--mistral-small-instruct,mistralai--mistral-medium-instruct,"
        "mistralai--mistral-small,cohere--command-a-reasoning,sonar-pro,sonar,sap-abap-1"
    )

    def available_models_list(self) -> List[str]:
        return [m.strip() for m in self.available_models.split(",") if m.strip()]

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
            logger.warning(f"Failed to parse sap_key.json: {e}")

    return Settings(**overrides)


def load_allowed_keys() -> Dict[str, Dict[str, Any]]:
    keys_config: Dict[str, Dict[str, Any]] = {}

    if os.path.exists("allowed_keys.json"):
        try:
            with open("allowed_keys.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for k in data:
                        keys_config[k] = {}
                elif isinstance(data, dict):
                    if "keys" in data and isinstance(data["keys"], list):
                        for k in data["keys"]:
                            keys_config[k] = {}
                    else:
                        for k, v in data.items():
                            keys_config[k] = v if isinstance(v, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to parse allowed_keys.json: {e}")

    env_keys = os.environ.get("ALLOWED_API_KEYS", "")
    if env_keys:
        for k in env_keys.split(","):
            k = k.strip()
            if k and k not in keys_config:
                keys_config[k] = {}

    return keys_config


def save_allowed_keys(keys_config: Dict[str, Dict[str, Any]]):
    """Save allowed keys back to allowed_keys.json."""
    try:
        with open("allowed_keys.json", "w", encoding="utf-8") as f:
            json.dump(keys_config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save allowed_keys.json: {e}")
        raise
