import io
import json
import os
import base64
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect as ws_connect

from config import Config

logger = logging.getLogger("pixelpilot.client")


class RateLimitError(RuntimeError):
    def __init__(
        self, message: str, remaining: Optional[int] = None, limit: Optional[int] = None
    ):
        super().__init__(message)
        self.remaining = remaining
        self.limit = limit


class DirectGeminiClient:
    def __init__(self, api_key: Optional[str] = None):
        from google import genai

        self._api_key = api_key or Config.GEMINI_API_KEY
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=self._api_key)

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: Optional[Dict[str, Any]] = None,
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
                    real_tools.append(
                        types.Tool(code_execution=types.ToolCodeExecution())
                    )

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
                    (
                        DirectGeminiClient._sanitize_schema(item)
                        if isinstance(item, dict)
                        else item
                    )
                    for item in value
                ]
        return new_schema


class BackendClient:
    def __init__(self, base_url: Optional[str] = None):
        from auth_manager import get_auth_manager

        self._get_auth = get_auth_manager
        self.base_url = (base_url or Config.BACKEND_URL).rstrip("/")

    def _ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        if not parts.scheme or not parts.netloc:
            raise RuntimeError(f"Invalid BACKEND_URL: {self.base_url}")

        scheme = "wss" if parts.scheme == "https" else "ws"
        base_path = parts.path.rstrip("/")
        ws_path = f"{base_path}/ws/generate" if base_path else "/ws/generate"
        return urlunsplit((scheme, parts.netloc, ws_path, "", ""))

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        auth = self._get_auth()
        if not auth.access_token:
            raise RuntimeError("Not signed in. Please log in to continue.")

        ws_url = self._ws_url()
        try:
            with ws_connect(
                ws_url, open_timeout=10, close_timeout=5, max_size=20_000_000
            ) as ws:
                ws.send(json.dumps({"type": "auth", "token": auth.access_token}))
                auth_resp = json.loads(ws.recv())
                if auth_resp.get("type") == "error":
                    code = int(auth_resp.get("code", 500))
                    detail = auth_resp.get("detail", "Authentication failed")
                    if code == 401:
                        auth.logout()
                        raise RuntimeError("Session expired. Please log in again.")
                    raise RuntimeError(str(detail))
                if auth_resp.get("type") != "auth_ok":
                    raise RuntimeError("Backend authentication handshake failed")

                ws.send(
                    json.dumps(
                        {
                            "type": "generate",
                            "request": {
                                "model": model,
                                "contents": contents,
                                "config": config,
                            },
                        }
                    )
                )
                response = json.loads(ws.recv())

                if response.get("type") == "error":
                    code = int(response.get("code", 500))
                    detail_payload = response.get("detail", "Request failed")
                    if isinstance(detail_payload, dict):
                        detail = str(detail_payload.get("message") or "Request failed")
                        limit = detail_payload.get("limit")
                        remaining = detail_payload.get("remaining")
                    else:
                        detail = str(detail_payload)
                        limit = None
                        remaining = None

                    if code == 401:
                        auth.logout()
                        raise RuntimeError("Session expired. Please log in again.")
                    if code == 429:
                        raise RateLimitError(detail, remaining=remaining, limit=limit)
                    raise RuntimeError(detail)

                if response.get("type") != "generate_result":
                    raise RuntimeError("Unexpected backend response type")

                result = response.get("data") or {}
                if not isinstance(result, dict):
                    return {"text": str(result)}
                return result
        except RateLimitError:
            raise
        except RuntimeError:
            raise
        except Exception as e:
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
