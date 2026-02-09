import os
import io
import json
import base64
from typing import List, Optional, Any, Dict
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in backend/.env")

client = genai.Client(api_key=API_KEY)


class GenerationRequest(BaseModel):
    model: str
    contents: List[Dict[str, Any]]  # This will be the raw JSON structure for contents
    config: Optional[Dict[str, Any]] = None


def _decode_image(data_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data_b64)))


def _process_part(part: Dict[str, Any]) -> types.Part:
    """
    Convert a dict part (from JSON) back to a types.Part.
    """
    if "text" in part:
        return types.Part(text=part["text"])
    elif "data" in part and "mime_type" in part:
        return types.Part.from_bytes(
            data=base64.b64decode(part["data"]), mime_type=part["mime_type"]
        )
    return types.Part(text=str(part))


def _process_contents(contents_data: List[Dict[str, Any]]) -> List[types.Content]:
    """
    Convert list of dicts (roles/parts) to list of types.Content.
    """
    contents = []
    for c in contents_data:
        role = c.get("role", "user")
        parts_data = c.get("parts", [])

        real_parts = []
        if isinstance(parts_data, list):
            for p in parts_data:
                real_parts.append(_process_part(p))
        elif isinstance(parts_data, dict):
            real_parts.append(_process_part(parts_data))
        else:
            real_parts.append(types.Part(text=str(parts_data)))

        contents.append(types.Content(role=role, parts=real_parts))
    return contents


def _sanitize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove 'additionalProperties' fields from schema as they are not supported by Gemini API.
    Recursive function to handle nested objects.
    """
    if not isinstance(schema, dict):
        return schema

    # Create a copy to avoid modifying the original
    new_schema = schema.copy()

    if "additionalProperties" in new_schema:
        del new_schema["additionalProperties"]

    for key, value in new_schema.items():
        if isinstance(value, dict):
            new_schema[key] = _sanitize_schema(value)
        elif isinstance(value, list):
            new_schema[key] = [
                _sanitize_schema(item) if isinstance(item, dict) else item
                for item in value
            ]

    return new_schema


async def generate_content(request: GenerationRequest):
    print(f"DEBUG: Processing request for model {request.model}")
    # print(f"DEBUG: Config keys: {request.config.keys() if request.config else 'None'}")

    contents = _process_contents(request.contents)

    config_data = request.config or {}

    # Extract tools if present
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
        config_data["response_schema"] = _sanitize_schema(schema)


    thinking_conf = config_data.pop("thinking_config", None)
    real_thinking_config = None
    if thinking_conf:
        real_thinking_config = types.ThinkingConfig(**thinking_conf)

    conf = types.GenerateContentConfig(
        **config_data, tools=real_tools, thinking_config=real_thinking_config
    )

    response = client.models.generate_content(
        model=request.model, contents=contents, config=conf
    )

    return {"text": response.text}
