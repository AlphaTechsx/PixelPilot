from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.provider_catalog import (
    PROVIDER_BASE_URL_ENVS,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_KEY_ENVS,
    litellm_model_name,
    normalize_provider_id,
)

__all__ = [
    "ModelCapabilities",
    "ProviderConfig",
    "PROVIDER_KEY_ENVS",
    "normalize_provider_id",
    "litellm_model_name",
    "resolve_request_provider",
    "resolve_live_provider",
    "get_request_provider_config",
    "get_live_provider_config",
    "live_provider_is_direct",
    "provider_catalog_payload",
]


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    realtime: bool = False
    request: bool = True
    text_input: bool = True
    image_input: bool = False
    audio_input: bool = False
    video_input: bool = False
    text_output: bool = True
    audio_output: bool = False
    tool_calling: bool = True

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_id: str
    display_name: str
    mode_kind: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    base_url: str = ""
    capabilities: ModelCapabilities = ModelCapabilities()

    @property
    def is_local(self) -> bool:
        return self.provider_id == "ollama"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["has_api_key"] = bool(str(self.api_key or "").strip())
        payload.pop("api_key", None)
        return payload


def get_request_provider_config(
    *,
    provider_id: str | None = None,
    model: str | None = None,
) -> ProviderConfig:
    from config import Config

    provider = normalize_provider_id(provider_id or Config.MODEL_PROVIDER)
    selected_model = str(model or Config.MODEL_NAME or _DEFAULT_REQUEST_MODELS[provider]).strip()
    if provider != "gemini" and selected_model == _DEFAULT_REQUEST_MODELS["gemini"]:
        selected_model = _DEFAULT_REQUEST_MODELS[provider]
    return _build_provider_config(
        provider_id=provider,
        mode_kind="request",
        model=selected_model,
        config=Config,
    )


resolve_request_provider = get_request_provider_config


def get_live_provider_config(
    *,
    provider_id: str | None = None,
    model: str | None = None,
) -> ProviderConfig:
    from config import Config

    provider = normalize_provider_id(provider_id or Config.LIVE_PROVIDER)
    explicit_live_model = str(model or "").strip()
    if not explicit_live_model:
        live_model = str(Config.LIVE_MODEL or "").strip()
        if provider in {"gemini", "openai"}:
            explicit_live_model = live_model or _DEFAULT_LIVE_MODELS.get(provider, "")
        else:
            explicit_live_model = str(Config.MODEL_NAME or "").strip()
            if not explicit_live_model or explicit_live_model == _DEFAULT_REQUEST_MODELS["gemini"]:
                explicit_live_model = _DEFAULT_REQUEST_MODELS.get(provider, "")
    selected_model = explicit_live_model
    if provider == "openai" and selected_model == _DEFAULT_LIVE_MODELS["gemini"]:
        selected_model = _DEFAULT_LIVE_MODELS["openai"]
    if provider not in {"gemini", "openai"} and selected_model == _DEFAULT_LIVE_MODELS["gemini"]:
        selected_model = str(Config.MODEL_NAME or "").strip() or _DEFAULT_REQUEST_MODELS.get(provider, selected_model)
    if not selected_model:
        selected_model = _DEFAULT_REQUEST_MODELS.get(provider, "")
    return _build_provider_config(
        provider_id=provider,
        mode_kind="realtime" if provider in {"gemini", "openai"} else "request",
        model=selected_model,
        config=Config,
    )


resolve_live_provider = get_live_provider_config


def live_provider_is_direct() -> bool:
    provider = get_live_provider_config()
    if provider.provider_id == "gemini":
        return bool(provider.api_key)
    if provider.provider_id == "openai":
        return bool(provider.api_key)
    return bool(provider.mode_kind == "request" and (provider.api_key or provider.is_local))


def provider_catalog_payload() -> list[dict[str, Any]]:
    result = []
    for pid, display in PROVIDER_DISPLAY_NAMES.items():
        result.append({
            "provider_id": pid,
            "display_name": display,
            "api_key_env": PROVIDER_KEY_ENVS.get(pid, ""),
            "has_base_url": pid in PROVIDER_BASE_URL_ENVS,
            "is_local": pid == "ollama",
        })
    return result


def _build_provider_config(*, provider_id: str, mode_kind: str, model: str, config: Any) -> ProviderConfig:
    import os
    api_key_env = PROVIDER_KEY_ENVS.get(provider_id, "")
    api_key = str(getattr(config, api_key_env, "") or "") if api_key_env else ""
    if provider_id == "gemini" and not api_key:
        api_key = str(getattr(config, "GEMINI_API_KEY", "") or "")
    base_url_env = PROVIDER_BASE_URL_ENVS.get(provider_id, "")
    base_url_attr = base_url_env
    base_url = str(getattr(config, base_url_attr, "") or "") if base_url_attr else ""
    return ProviderConfig(
        provider_id=provider_id,
        display_name=PROVIDER_DISPLAY_NAMES[provider_id],
        mode_kind=mode_kind,
        model=model,
        api_key_env=api_key_env,
        api_key=api_key,
        base_url=base_url,
        capabilities=_capabilities_for(provider_id, mode_kind=mode_kind),
    )


def _capabilities_for(provider_id: str, *, mode_kind: str) -> ModelCapabilities:
    provider = normalize_provider_id(provider_id)
    if mode_kind == "realtime" and provider == "gemini":
        return ModelCapabilities(
            realtime=True,
            request=True,
            image_input=True,
            audio_input=True,
            video_input=True,
            audio_output=True,
            tool_calling=True,
        )
    if mode_kind == "realtime" and provider == "openai":
        return ModelCapabilities(
            realtime=True,
            request=True,
            image_input=True,
            audio_input=True,
            text_output=True,
            audio_output=True,
            tool_calling=True,
        )
    return ModelCapabilities(request=True, image_input=True, tool_calling=True)


_DEFAULT_REQUEST_MODELS: dict[str, str] = {
    "gemini": "gemini-3-flash-preview",
    "openai": "gpt-5.4",
    "anthropic": "claude-sonnet-4-5",
    "xai": "grok-4",
    "openrouter": "openai/gpt-5.4",
    "ollama": "llama3.2",
    "openai_compatible": "gpt-oss-20b",
    "vercel_ai_gateway": "openai/gpt-5.4",
}

_DEFAULT_LIVE_MODELS: dict[str, str] = {
    "gemini": "gemini-3.1-flash-live-preview",
    "openai": "gpt-realtime",
}
