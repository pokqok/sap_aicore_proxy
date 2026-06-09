"""
Usage logging utility.
Records per-user token consumption to usage.log in JSONL format.
"""

import json
import logging
import datetime
import os
from typing import List, Dict, Optional

logger = logging.getLogger("sap-proxy")

USAGE_LOG_FILE = "usage.log"


async def log_usage(user_id: str, deployment_id: str, usage: dict):
    """Append a usage record to usage.log."""
    if not usage:
        return
    log_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user_id": user_id,
        "deployment_id": deployment_id,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    try:
        with open(USAGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write usage log: {e}")


def read_usage_logs(
    user_id: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 500,
) -> Dict:
    """Read and filter usage logs. Returns logs list and per-user summary."""
    logs: List[Dict] = []

    if not os.path.exists(USAGE_LOG_FILE):
        return {"logs": [], "summary": {}}

    try:
        with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Filter by user_id
                    if user_id and entry.get("user_id") != user_id:
                        continue
                    # Filter by date (YYYY-MM-DD prefix match on timestamp)
                    if date and not entry.get("timestamp", "").startswith(date):
                        continue
                    logs.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.error(f"Failed to read usage log: {e}")
        return {"logs": [], "summary": {}}

    # Build per-user summary
    summary: Dict[str, Dict] = {}
    for entry in logs:
        uid = entry.get("user_id", "unknown")
        if uid not in summary:
            summary[uid] = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "request_count": 0}
        summary[uid]["total_tokens"] += entry.get("total_tokens", 0)
        summary[uid]["prompt_tokens"] += entry.get("prompt_tokens", 0)
        summary[uid]["completion_tokens"] += entry.get("completion_tokens", 0)
        summary[uid]["request_count"] += 1

    # Apply limit (most recent first)
    logs = logs[-limit:][::-1]

    return {"logs": logs, "summary": summary}


def get_usage_stats() -> Dict:
    """Get aggregate usage statistics for the dashboard."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    total_requests = 0
    total_tokens = 0
    today_requests = 0
    today_tokens = 0

    if not os.path.exists(USAGE_LOG_FILE):
        return {
            "total_requests": 0, "total_tokens": 0,
            "today_requests": 0, "today_tokens": 0,
        }

    try:
        with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    tokens = entry.get("total_tokens", 0)
                    total_requests += 1
                    total_tokens += tokens
                    if entry.get("timestamp", "").startswith(today):
                        today_requests += 1
                        today_tokens += tokens
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "today_requests": today_requests,
        "today_tokens": today_tokens,
    }
