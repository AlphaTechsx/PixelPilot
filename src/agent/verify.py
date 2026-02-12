import io
import base64
from typing import Any, Dict, List, Optional
import logging
from PIL import Image
from config import Config
from backend_client import get_client
from pydantic import BaseModel, Field
from agent.prompts import VERIFY_TASK_COMPLETION_PROMPT

logger = logging.getLogger("pixelpilot.verify")


def verify_task_completion(
    user_command: str,
    expected_result: str,
    screen_elements: List[Dict],
    original_path: str,
    debug_path: str,
    reference_sheet,
    task_history: List[Dict],
) -> Optional[Dict[str, Any]]:
    """
    Verify that a task was actually completed by analyzing the current screen state.
    """
    try:
        clean_image = Image.open(original_path)
        annotated_image = Image.open(debug_path)
    except Exception as e:
        logger.error(f"Error loading images for verification: {e}")
        return None

    class VerificationResult(BaseModel):
        is_complete: bool = Field(
            description="True if the task is verifiably complete based on visual evidence"
        )
        confidence: float = Field(description="Confidence score spanning 0.0 to 1.0")
        reasoning: str = Field(
            description="Explanation of what visual evidence supports this conclusion"
        )
        next_action: Optional[str] = Field(
            description="Suggestion for the next step if task is not complete, or null/None if complete"
        )

    elements_str = "\n".join(
        [f"ID {el['id']}: {el['type']} '{el['label']}'" for el in screen_elements[:100]]
    )

    history_str = "\n".join(
        [
            f"Step {i + 1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(task_history)
        ]
    )

    prompt_text = VERIFY_TASK_COMPLETION_PROMPT.format(
        user_command=user_command,
        expected_result=expected_result,
        history_str=history_str,
        elements_str=elements_str,
    )

    def img_to_dict(img):
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        return {
            "mime_type": "image/png",
            "data": base64.b64encode(img_byte_arr.getvalue()).decode("utf-8"),
        }

    parts = [
        {"text": prompt_text},
        img_to_dict(clean_image),
        img_to_dict(annotated_image),
    ]
    if reference_sheet:
        parts.append(img_to_dict(reference_sheet))

    contents = [{"role": "user", "parts": parts}]

    try:
        client = get_client()
        response_data = client.generate_content(
            model=Config.GEMINI_MODEL,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": VerificationResult.model_json_schema(),
            },
        )

        result_obj = VerificationResult.model_validate_json(response_data["text"])
        return result_obj.model_dump()

    except Exception as e:
        logger.error(f"Error during verification: {e}")
        return None
