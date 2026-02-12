import io
import json
import os
import base64
import logging
from typing import Any, Dict, Optional
from urllib import request, error

from config import Config

logger = logging.getLogger("pixelpilot.client")


class RateLimitError(RuntimeError):
    def __init__(self, message: str, remaining: Optional[int] = None, limit: Optional[int] = None):
        super().__init__(message)
        self.remaining = remaining
        self.limit = limit


def _parse_error_detail(body: str) -> str:
    if not body:
        return "Request failed"
    try:
        data = json.loads(body)
        return data.get("detail", "Request failed")
    except Exception:
        return body.strip() or "Request failed"


class DirectGeminiClient:
    def __init__(self, api_key: Optional[str] = None):
        from google import genai
        self._api_key = api_key or Config.GEMINI_API_KEY
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=self._api_key)

    def generate_content(
        self, *, model: str, contents: list[dict], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from google.genai import types

        processed_contents = []
        for c in contents:
            role = c.get("role", "user")
            parts_data = c.get("parts", [])
            real_parts = []
            if isinstance(parts_data, list):
                for p in parts_data:
                    real_parts.append(self._process_part(p, types))
            elif isinstance(parts_data, dict):
                real_parts.append(self._process_part(parts_data, types))
            else:
                real_parts.append(types.Part(text=str(parts_data)))
            processed_contents.append(types.Content(role=role, parts=real_parts))

        config_data = dict(config) if config else {}

        tools_config = config_data.pop("tools", None)
        real_tools = None
        if tools_config:
            real_tools = []
            for t in tools_config:
                if "google_search" in t:
                    real_tools.append(types.Tool(google_search=types.GoogleSearch()))
                if "code_execution" in t:
                    real_tools.append(types.Tool(code_execution=types.ToolCodeExecution()))

        if "response_json_schema" in config_data:
            schema = config_data.pop("response_json_schema")
            config_data["response_schema"] = self._sanitize_schema(schema)

        thinking_conf = config_data.pop("thinking_config", None)
        real_thinking_config = None
        if thinking_conf:
            real_thinking_config = types.ThinkingConfig(**thinking_conf)

        conf = types.GenerateContentConfig(
            **config_data, tools=real_tools, thinking_config=real_thinking_config
        )

        try:
            response = self._client.models.generate_content(
                model=model, contents=processed_contents, config=conf
            )
            return {"text": response.text}
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}") from e
            raise RuntimeError(f"Gemini API error: {e}") from e

    @staticmethod
    def _process_part(part: dict, types_module) -> Any:
        if "text" in part:
            return types_module.Part(text=part["text"])
        if "data" in part and "mime_type" in part:
            return types_module.Part.from_bytes(
                data=base64.b64decode(part["data"]), mime_type=part["mime_type"]
            )
        return types_module.Part(text=str(part))

    @staticmethod
    def _sanitize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return schema
        new_schema = schema.copy()
        new_schema.pop("additionalProperties", None)
        for key, value in new_schema.items():
            if isinstance(value, dict):
                new_schema[key] = DirectGeminiClient._sanitize_schema(value)
            elif isinstance(value, list):
                new_schema[key] = [
                    DirectGeminiClient._sanitize_schema(item) if isinstance(item, dict) else item
                    for item in value
                ]
        return new_schema


class BackendClient:
    def __init__(self, base_url: Optional[str] = None):
        from auth_manager import get_auth_manager
        self._get_auth = get_auth_manager
        self.base_url = (base_url or Config.BACKEND_URL).rstrip("/")

    def generate_content(
        self, *, model: str, contents: list[dict], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")

        payload = {"model": model, "contents": contents, "config": config}
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth.access_token}",
        }
        url = f"{self.base_url}/v1/generate"
        req = request.Request(url, data=data, method="POST", headers=headers)

        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                pass

            detail = _parse_error_detail(body)
            if e.code == 401:
                auth.logout()
                raise RuntimeError("Session expired. Please log in again.") from e
            if e.code == 429:
                limit = None
                remaining = None
                try:
                    limit = int(e.headers.get("X-RateLimit-Limit", "0") or 0)
                    remaining = int(e.headers.get("X-RateLimit-Remaining", "0") or 0)
                except Exception:
                    pass
                raise RateLimitError(detail, remaining=remaining, limit=limit) from e
            raise RuntimeError(detail) from e
        except error.URLError as e:
            raise RuntimeError("Backend unavailable. Is it running?") from e


_client_instance: Optional[Any] = None


def get_client():
    global _client_instance
    if _client_instance is None:
        if Config.USE_DIRECT_API:
            logger.info("Using direct Gemini API (API key configured)")
            _client_instance = DirectGeminiClient()
        else:
            logger.info("Using backend proxy for Gemini API")
            _client_instance = BackendClient()
    return _client_instance
