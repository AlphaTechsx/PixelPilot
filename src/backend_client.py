import requests
import base64
import json
import os
from io import BytesIO
from typing import List, Dict, Any, Optional
from config import Config


class RateLimitError(Exception):
    """Raised when API rate limit is exceeded."""

    pass


class BackendClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("BACKEND_URL", "http://localhost:8000")

    def generate_content(
        self, model: str, contents: List[Any], config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send generation request to backend.
        'contents' here mimics the google.genai structure but needs to be serialized.
        We expect 'contents' to be a list of dicts or objects that we can map.
        """
        serialized_contents = self._serialize_contents(contents)
        serialized_config = self._serialize_config(config)

        payload = {
            "model": model,
            "contents": serialized_contents,
            "config": serialized_config,
        }

        import time
        from auth_manager import get_auth_manager

        max_retries = 5
        base_delay = 5  # seconds
        auth = get_auth_manager()

        for attempt in range(max_retries):
            try:
                # Build headers with auth token
                headers = {}
                if auth.token:
                    headers["Authorization"] = f"Bearer {auth.token}"

                response = requests.post(
                    f"{self.base_url}/v1/generate",
                    json=payload,
                    headers=headers,
                )

                # Handle 401 Unauthorized
                if response.status_code == 401:
                    raise RuntimeError("Authentication required. Please login again.")

                # Handle 429 Rate Limit - stop immediately
                if response.status_code == 429:
                    raise RateLimitError(
                        "⚠️ Daily request limit reached!\n\n"
                        "You have used all your requests for today. "
                        "Limit resets at midnight UTC."
                    )

                response.raise_for_status()
                return response.json()  # Expect {"text": "..."}

            except requests.exceptions.RequestException as e:
                # If it's not a 429 (handled above) or if we ran out of retries for 429
                print(f"Backend Request Error: {e}")
                if "response" in locals() and response is not None:
                    print(f"Response: {response.text}")

                # If we are on the last attempt, raise the error
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Backend communication failed: {e}")

                # For other errors, maybe we shouldn't retry?
                # But typically 500s might be transient.
                # For now, let's only retry 429s (handled by continue) and maybe 500s?
                # Let's stick to 429 for now.
                raise RuntimeError(f"Backend communication failed: {e}")

    def _serialize_contents(self, contents: List[Any]) -> List[Dict[str, Any]]:
        """
        Convert SDK-like contents (which might contain PIL images or types.Part)
        into JSON-serializable dicts.
        """
        serialized = []

        # Check if this is a flat list of parts (common in simple generation calls)
        # e.g. [{"text": "..."}, {"data": "..."}] without "role" wrapper
        is_flat_parts = False
        if isinstance(contents, list) and contents:
            first = contents[0]
            if (
                isinstance(first, dict)
                and "parts" not in first
                and ("text" in first or "data" in first or "mime_type" in first)
            ):
                is_flat_parts = True
            elif not isinstance(first, dict) and not hasattr(first, "parts"):
                # Assuming non-dict objects without 'parts' attr are parts themselves (like types.Part or strings)
                is_flat_parts = True

        if is_flat_parts:
            # Wrap in a single user content block
            ser_parts = [self._serialize_part(p) for p in contents]
            serialized.append({"role": "user", "parts": ser_parts})
            return serialized

        for c in contents:
            if isinstance(c, dict):
                # Request is likely valid Content dict -> preserve structure
                role = c.get("role", "user")
                parts = c.get("parts", [])
                ser_parts = [self._serialize_part(p) for p in parts]
                serialized.append({"role": role, "parts": ser_parts})
            # Handle if 'types.Content' object is passed
            elif hasattr(c, "parts"):
                role = getattr(c, "role", "user")
                parts = getattr(c, "parts", [])
                ser_parts = [self._serialize_part(p) for p in parts]
                serialized.append({"role": role, "parts": ser_parts})
            else:
                # Fallback for unexpected objects, try to treat as a part wrapped in content
                serialized.append({"role": "user", "parts": [self._serialize_part(c)]})

        return serialized

    def _serialize_part(self, part: Any) -> Dict[str, Any]:
        # Identify if it's a types.Part object by checking attributes
        # Since we removed the SDK import, the client code (brain.py) will probably
        # still try to use the SDK types unless we replace them.
        # KEY STEP: `brain.py` still imports `google.genai`. We need to Mock/Stub that or
        # change `brain.py` to simply use dicts or a local helper.

        # Ideally, `brain.py` should construct simple dicts or we provide a local `Part` class.

        if isinstance(part, str):
            return {"text": part}

        # If it has 'text' attr:
        if hasattr(part, "text") and part.text:
            return {"text": part.text}

        # If it has 'inline_data' or data/mime_type
        if hasattr(part, "inline_data") and part.inline_data:
            return {
                "data": base64.b64encode(part.inline_data.data).decode("utf-8"),
                "mime_type": part.inline_data.mime_type,
            }

        # Check for our own "from_bytes" style
        # In brain.py: types.Part.from_bytes(data=..., mime_type=...)
        # We need to see how types.Part stores this.
        # Usually it's 'inline_data' or 'blob'.

        # Fallback for checking if it is a dict (if we changed brain.py to use dicts)
        if isinstance(part, dict):
            return part

        # Inspect for hidden fields if it's the real SDK object
        # ...

        raise ValueError(f"Unknown part type: {type(part)}")

    def _serialize_config(self, config: Optional[Dict]) -> Optional[Dict]:
        if not config:
            return None

        # Deep copy to avoid mutating original
        new_conf = config.copy()

        # config might contain 'tools' which are SDK objects.
        # e.g. [types.Tool(google_search=...)]
        # We need to convert them to simple flags/dicts.
        if "tools" in new_conf:
            ser_tools = []
            for t in new_conf["tools"]:
                # Check what tool it is
                # SDK objects usually have 'google_search' attribute
                if hasattr(t, "google_search") and t.google_search is not None:
                    ser_tools.append({"google_search": {}})
                if hasattr(t, "code_execution") and t.code_execution is not None:
                    ser_tools.append({"code_execution": {}})
            new_conf["tools"] = ser_tools

        return new_conf
