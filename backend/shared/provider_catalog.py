from __future__ import annotations

from typing import Any, Callable


PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "xai": "xAI",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "openai_compatible": "OpenAI-compatible",
    "vercel_ai_gateway": "Vercel AI Gateway",
}

PROVIDER_KEY_ENVS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "vercel_ai_gateway": "VERCEL_AI_GATEWAY_API_KEY",
}

PROVIDER_BASE_URL_ENVS: dict[str, str] = {
    "openai_compatible": "OPENAI_COMPATIBLE_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
    "vercel_ai_gateway": "VERCEL_AI_GATEWAY_BASE_URL",
}

PROVIDER_BASE_URL_DEFAULTS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "vercel_ai_gateway": "https://ai-gateway.vercel.sh/v1",
}

LITELLM_PREFIX_RULES: dict[str, str] = {
    "gemini": "gemini",
    "anthropic": "anthropic",
    "xai": "xai",
    "openrouter": "openrouter",
    "ollama": "ollama",
}

_PROVIDER_ALIASES: dict[str, str] = {
    "google": "gemini",
    "google-gemini": "gemini",
    "claude": "anthropic",
    "anthropic-claude": "anthropic",
    "grok": "xai",
    "x.ai": "xai",
    "openai-compatible": "openai_compatible",
    "compatible": "openai_compatible",
    "vercel": "vercel_ai_gateway",
    "vercel-ai-gateway": "vercel_ai_gateway",
    "ai-gateway": "vercel_ai_gateway",
}


def normalize_provider_id(value: Any, *, default: str = "gemini") -> str:
    key = str(value or "").strip().lower()
    key = _PROVIDER_ALIASES.get(key, key)
    if not key:
        return default
    return key if key in PROVIDER_DISPLAY_NAMES else default


def litellm_model_name(provider_id: str, model: str) -> str:
    provider = normalize_provider_id(provider_id)
    clean_model = str(model or "").strip()
    if "/" in clean_model:
        return clean_model
    prefix = LITELLM_PREFIX_RULES.get(provider)
    if prefix:
        return f"{prefix}/{clean_model}"
    return clean_model


def api_key_for(provider: str, env_getter: Callable[[str, str], str]) -> str:
    env_name = PROVIDER_KEY_ENVS.get(provider, "")
    return str(env_getter(env_name, "")).strip() if env_name else ""


def base_url_for(provider: str, env_getter: Callable[[str, str], str]) -> str:
    env_name = PROVIDER_BASE_URL_ENVS.get(provider, "")
    if not env_name:
        return ""
    default = PROVIDER_BASE_URL_DEFAULTS.get(provider, "")
    return str(env_getter(env_name, default)).strip()
