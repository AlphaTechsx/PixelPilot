from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from auth_manager import get_auth_manager
from config import Config
from model_providers import (
    PROVIDER_KEY_ENVS as _PROVIDER_KEY_ENV,
    get_live_provider_config,
    get_request_provider_config,
    normalize_provider_id,
)


def get_auth_state() -> dict[str, Any]:
    auth = get_auth_manager()
    signed_in = bool(auth.is_logged_in and auth.access_token)
    request_provider = get_request_provider_config()
    live_provider = get_live_provider_config()
    has_direct_credentials = bool(
        request_provider.api_key
        or live_provider.api_key
        or request_provider.is_local
        or live_provider.is_local
    )
    return {
        "signedIn": signed_in,
        "directApi": bool(Config.USE_DIRECT_API and has_direct_credentials),
        "email": str(auth.email or ""),
        "userId": str(auth.user_id or ""),
        "backendUrl": str(Config.BACKEND_URL or ""),
        "hasApiKey": bool(has_direct_credentials),
        "needsAuth": not bool(Config.USE_DIRECT_API and has_direct_credentials) and not signed_in,
        "requestProvider": request_provider.as_dict(),
        "liveProvider": live_provider.as_dict(),
    }


def save_api_key(
    value: str,
    *,
    provider_id: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provider = normalize_provider_id(provider_id or Config.MODEL_PROVIDER)
    if provider == "ollama":
        api_key = ""
    else:
        api_key = str(value or "").strip()
        if not api_key:
            raise RuntimeError("Please enter an API key.")

    key_env = _PROVIDER_KEY_ENV.get(provider)
    if provider != "ollama" and not key_env:
        raise RuntimeError(f"Direct API key storage is not supported for provider: {provider}")

    base_url = str(base_url or "").strip()
    model = str(model or "").strip()
    env_updates: dict[str, str] = {
        "PIXELPILOT_MODEL_PROVIDER": provider,
        "PIXELPILOT_LIVE_PROVIDER": provider,
    }
    if key_env:
        env_updates[key_env] = api_key
    if model:
        env_updates["PIXELPILOT_MODEL"] = model
    if provider == "ollama" and base_url:
        env_updates["OLLAMA_BASE_URL"] = base_url
    elif provider == "openai_compatible" and base_url:
        env_updates["OPENAI_COMPATIBLE_BASE_URL"] = base_url
    elif provider == "vercel_ai_gateway" and base_url:
        env_updates["VERCEL_AI_GATEWAY_BASE_URL"] = base_url

    env_path = Path(Config.PROJECT_ROOT) / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

    update_prefixes = tuple(f"{name}=" for name in env_updates)
    filtered = [line for line in lines if not line.strip().startswith(update_prefixes)]
    if filtered and not filtered[-1].endswith("\n"):
        filtered.append("\n")
    for name, stored_value in env_updates.items():
        filtered.append(f"{name}={stored_value}\n")
    env_path.write_text("".join(filtered), encoding="utf-8")

    for name, stored_value in env_updates.items():
        os.environ[name] = stored_value

    Config.MODEL_PROVIDER = provider
    Config.LIVE_PROVIDER = provider
    if key_env:
        setattr(Config, key_env, api_key)
    if model:
        Config.MODEL_NAME = model
    if provider == "ollama" and base_url:
        Config.OLLAMA_BASE_URL = base_url
    elif provider == "openai_compatible" and base_url:
        Config.OPENAI_COMPATIBLE_BASE_URL = base_url
    elif provider == "vercel_ai_gateway" and base_url:
        Config.VERCEL_AI_GATEWAY_BASE_URL = base_url
    Config.USE_DIRECT_API = True
    try:
        from backend_client import reset_client

        reset_client()
    except Exception:
        pass
    return get_auth_state()


def start_browser_flow(mode: str) -> dict[str, Any]:
    return get_auth_manager().start_browser_flow(mode)


def exchange_desktop_code(code: str, state: str = "") -> dict[str, Any]:
    get_auth_manager().exchange_desktop_code(code, state or None)
    return get_auth_state()


def logout_all() -> dict[str, Any]:
    Config.clear_api_key()
    try:
        from backend_client import reset_client

        reset_client()
    except Exception:
        pass
    get_auth_manager().logout()
    return get_auth_state()
