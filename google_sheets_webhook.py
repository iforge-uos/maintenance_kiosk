from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


def load_env_file() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def sync_enabled() -> bool:
    load_env_file()
    value = os.environ.get("GOOGLE_SHEETS_SYNC_ENABLED", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def webhook_config() -> tuple[str, str]:
    load_env_file()
    webhook_url = os.environ.get("GOOGLE_SHEETS_WEBHOOK_URL", "").strip()
    secret = os.environ.get("GOOGLE_SHEETS_WEBHOOK_SECRET", "").strip()
    if not webhook_url:
        raise ValueError("GOOGLE_SHEETS_WEBHOOK_URL is not configured")
    if not webhook_url.startswith("https://"):
        raise ValueError("GOOGLE_SHEETS_WEBHOOK_URL must start with https://")
    if not secret:
        raise ValueError("GOOGLE_SHEETS_WEBHOOK_SECRET is not configured")
    return webhook_url, secret


def send_payload(payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    webhook_url, secret = webhook_config()
    body = dict(payload)
    body["secret"] = secret
    data = json.dumps(body, default=str).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Sheets webhook returned HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Google Sheets webhook request failed: {exc.reason}") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"Google Sheets webhook returned HTTP {status}: {response_body}")
    if not response_body.strip():
        return {"ok": True}

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return {"ok": True, "body": response_body}

    if parsed.get("ok") is False or parsed.get("success") is False:
        raise RuntimeError(f"Google Sheets webhook rejected payload: {response_body}")
    return parsed
