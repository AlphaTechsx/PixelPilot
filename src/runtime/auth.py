from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from auth_manager import get_auth_manager
from config import Config


def get_auth_state() -> dict[str, Any]:
    auth = get_auth_manager()
    signed_in = bool(auth.is_logged_in and auth.access_token)
    return {
        "signedIn": signed_in,
        "directApi": bool(Config.USE_DIRECT_API and Config.GEMINI_API_KEY),
        "email": str(auth.email or ""),
        "userId": str(auth.user_id or ""),
        "backendUrl": str(Config.BACKEND_URL or ""),
        "hasApiKey": bool(Config.GEMINI_API_KEY),
        "needsAuth": not bool(Config.USE_DIRECT_API and Config.GEMINI_API_KEY) and not signed_in,
    }


def save_api_key(value: str) -> dict[str, Any]:
    api_key = str(value or "").strip()
    if not api_key:
        raise RuntimeError("Please enter an API key.")
    if not api_key.startswith("AIza"):
        raise RuntimeError("Invalid API key format (should start with AIza).")

    env_path = Path(Config.PROJECT_ROOT) / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

    filtered = [line for line in lines if not line.strip().startswith("GEMINI_API_KEY=")]
    if filtered and not filtered[-1].endswith("\n"):
        filtered.append("\n")
    filtered.append(f"GEMINI_API_KEY={api_key}\n")
    env_path.write_text("".join(filtered), encoding="utf-8")

    os.environ["GEMINI_API_KEY"] = api_key
    Config.GEMINI_API_KEY = api_key
    Config.USE_DIRECT_API = True
    Config.LIVE_MODE_AVAILABLE = bool(Config.ENABLE_GEMINI_LIVE_MODE)
    return get_auth_state()


def logout_all() -> dict[str, Any]:
    Config.clear_api_key()
    get_auth_manager().logout()
    return get_auth_state()
