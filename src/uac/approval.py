from __future__ import annotations

import base64
import io
import json
import logging
import os
import threading
from typing import Any, Callable, Optional

import PIL.Image
from pydantic import BaseModel, Field

from agent.prompts import UAC_APPROVAL_PROMPT
from backend_client import get_client
from config import Config


logger = logging.getLogger("pixelpilot.uac.approval")
_client = get_client()


class _ModelWrapper:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def generate_content(self, contents, config=None):
        return _client.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )


class _UACApprovalDecision(BaseModel):
    allow: bool = Field(
        description=(
            "True only if this prompt should be auto-allowed without user confirmation."
        )
    )
    expected_match: bool = Field(
        description=(
            "True only if prompt target clearly matches expected action context."
        )
    )
    confidence: float = Field(
        description="Confidence score in [0,1] for the expected-action match."
    )
    reasoning: str = Field(
        description="Short security reasoning based on program/publisher and intent match."
    )


def _get_model() -> _ModelWrapper:
    return _ModelWrapper(Config.GEMINI_MODEL)


def _emit_status_note(callback: Optional[Callable[[str], None]], message: str) -> None:
    if callback is None:
        return
    clean = str(message or "").strip()
    if not clean:
        return
    try:
        callback(clean)
    except Exception:
        logger.debug("Failed to emit UAC approval status note", exc_info=True)


def _image_to_part(image_obj: PIL.Image.Image) -> dict[str, str]:
    image_buffer = io.BytesIO()
    image_obj.save(image_buffer, format="PNG")
    return {
        "mime_type": "image/png",
        "data": base64.b64encode(image_buffer.getvalue()).decode("utf-8"),
    }


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _build_uac_prompt_text(
    *,
    prompt_state: Optional[dict[str, Any]],
    action_name: str,
    expected_intent: str,
) -> str:
    state = dict(prompt_state or {})
    expected = str(expected_intent or "").strip()
    if not expected:
        expected = "No expected action context was provided by runtime."

    context_lines = [
        f"Blocked action label: {str(action_name or 'desktop action').strip() or 'desktop action'}",
        f"Expected action context: {expected}",
        f"Input desktop: {str(state.get('inputDesktop') or 'unknown')}",
        f"Detector confidence: {str(state.get('confidence') or 'unknown')}",
        f"Secure desktop active: {bool(state.get('secureDesktopActive'))}",
        f"Consent PID list: {state.get('consentProcessPids') or []}",
        f"UAC UI PID list: {state.get('uacUiProcessPids') or []}",
    ]

    strict_rules = (
        "STRICT POLICY:\n"
        "1. Set expected_match=true only when program/publisher in the UAC prompt aligns with the expected action context.\n"
        "2. Set allow=true only when expected_match=true AND the prompt looks trustworthy (legitimate target, trusted/signed publisher, no suspicious mismatch).\n"
        "3. If anything is unclear, set allow=false and expected_match=false."
    )

    response_shape = (
        '{ "allow": true|false, "expected_match": true|false, '
        '"confidence": 0.0-1.0, "reasoning": "..." }'
    )

    return (
        f"{UAC_APPROVAL_PROMPT.strip()}\n\n"
        f"{strict_rules}\n\n"
        "Prompt context (from detector/runtime):\n"
        + "\n".join(context_lines)
        + "\n\nRespond with JSON only in this shape:\n"
        + response_shape
    )


def ask_uac_brain(
    image_path: Optional[str] = None,
    *,
    prompt_state: Optional[dict[str, Any]] = None,
    action_name: str = "",
    expected_intent: str = "",
    status_note_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Run AI-assisted UAC risk assessment and strict expected-intent match check."""
    fallback = {
        "allow": False,
        "expected_match": False,
        "confidence": 0.0,
        "reasoning": "Unable to verify the secure desktop prompt safely.",
    }

    _emit_status_note(
        status_note_callback,
        "UAC: Reviewing secure desktop prompt screenshot against expected action.",
    )

    try:
        prompt_text = _build_uac_prompt_text(
            prompt_state=prompt_state,
            action_name=action_name,
            expected_intent=expected_intent,
        )

        parts: list[dict[str, Any]] = [{"text": prompt_text}]

        clean_path = str(image_path or "").strip()
        if clean_path and os.path.exists(clean_path):
            try:
                with PIL.Image.open(clean_path) as screenshot:
                    parts.append(_image_to_part(screenshot))
            except Exception:
                logger.debug("Failed to attach UAC screenshot for AI assessment", exc_info=True)
        else:
            logger.warning(
                "UAC_APPROVAL_IMAGE_MISSING path=%s",
                clean_path or "",
            )

        model = _get_model()
        response_data = model.generate_content(
            [{"role": "user", "parts": parts}],
            config={
                "response_mime_type": "application/json",
                "response_json_schema": _UACApprovalDecision.model_json_schema(),
            },
        )

        raw_text = ""
        if isinstance(response_data, dict):
            raw_text = str(response_data.get("text") or "")
        else:
            raw_text = str(response_data or "")
        raw_text = raw_text.strip()

        try:
            parsed = json.loads(raw_text or "{}")
        except Exception:
            logger.warning("UAC AI assessment returned non-JSON response")
            return fallback

        allow = parsed.get("allow")
        expected_match = parsed.get("expected_match")
        if not isinstance(allow, bool) or not isinstance(expected_match, bool):
            logger.warning("UAC AI assessment missing required boolean fields")
            return fallback

        confidence = _clamp_confidence(parsed.get("confidence"))
        reasoning = str(parsed.get("reasoning") or "").strip() or fallback["reasoning"]

        logger.info(
            "UAC_BRAIN_DECISION suggested=%s expected_match=%s confidence=%.2f reasoning=%s",
            "ALLOW" if allow else "DENY",
            expected_match,
            confidence,
            reasoning,
        )
        return {
            "allow": allow,
            "expected_match": expected_match,
            "confidence": confidence,
            "reasoning": reasoning,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("UAC AI approval assessment failed: %s", exc)
        fallback["reasoning"] = f"AI approval assessment failed: {exc}"
        return fallback


def _ask_confirmation_with_timeout(
    chat_window: Any,
    *,
    title: str,
    message: str,
    timeout_seconds: float,
) -> tuple[bool, bool]:
    done = threading.Event()
    result: dict[str, Any] = {"approved": False, "error": None}

    def _worker() -> None:
        try:
            result["approved"] = bool(chat_window.ask_confirmation(title, message))
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(
        target=_worker,
        name="UacConfirmationPrompt",
        daemon=True,
    )
    thread.start()

    completed = done.wait(max(0.1, float(timeout_seconds or 0.1)))
    if not completed:
        return False, True

    if result.get("error") is not None:
        logger.error("Failed to collect UAC confirmation: %s", result["error"])
        return False, False

    return bool(result.get("approved")), False


def confirm_uac_allow(
    *,
    chat_window: Any,
    reasoning: str,
    action_name: str,
    expected_intent: str,
    prompt_state: Optional[dict[str, Any]] = None,
    status_note_callback: Optional[Callable[[str], None]] = None,
    timeout_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Ask the user for final ALLOW confirmation when strict auto-allow did not pass."""
    if chat_window is None or not hasattr(chat_window, "ask_confirmation"):
        _emit_status_note(status_note_callback, "UAC confirmation UI unavailable. Defaulting to DENY.")
        return {"approved": False, "timed_out": False}

    state = dict(prompt_state or {})
    timeout_s = max(
        0.1,
        float(timeout_seconds or getattr(Config, "UAC_USER_CONFIRM_TIMEOUT_SECONDS", 5.0) or 5.0),
    )
    expected = str(expected_intent or "").strip() or "No expected action context provided."

    prompt_message = (
        "Windows User Account Control is active on the secure desktop.\n\n"
        f"Blocked action: {str(action_name or 'desktop action').strip() or 'desktop action'}\n"
        f"Expected action context: {expected}\n"
        f"Input desktop: {str(state.get('inputDesktop') or 'unknown')}\n\n"
        f"AI assessment:\n{str(reasoning or 'No reasoning provided.')}\n\n"
        f"Approve this elevation request? (auto-deny in {timeout_s:.0f}s)"
    )

    approved, timed_out = _ask_confirmation_with_timeout(
        chat_window,
        title="Approve Elevation",
        message=prompt_message,
        timeout_seconds=timeout_s,
    )

    if timed_out:
        _emit_status_note(
            status_note_callback,
            f"UAC confirmation timed out after {timeout_s:.0f}s. Sending DENY.",
        )
    return {
        "approved": bool(approved),
        "timed_out": bool(timed_out),
    }


def resolve_uac_allow_decision(
    *,
    prompt_state: Optional[dict[str, Any]],
    action_name: str,
    chat_window: Any,
    image_path: Optional[str] = None,
    expected_intent: str = "",
    status_note_callback: Optional[Callable[[str], None]] = None,
    user_timeout_seconds: Optional[float] = None,
) -> bool:
    """Resolve final UAC ALLOW/DENY using strict AI match first, then user fallback."""
    state = dict(prompt_state or {})
    snapshot_path = str(image_path or state.get("uac_snapshot_path") or "").strip() or None
    expected = str(expected_intent or "").strip()

    assessment = ask_uac_brain(
        image_path=snapshot_path,
        prompt_state=state,
        action_name=action_name,
        expected_intent=expected,
        status_note_callback=status_note_callback,
    )

    suggested_allow = bool(assessment.get("allow"))
    expected_match = bool(assessment.get("expected_match"))
    confidence = _clamp_confidence(assessment.get("confidence"))
    reasoning = str(assessment.get("reasoning") or "").strip()

    threshold = _clamp_confidence(
        getattr(Config, "UAC_EXPECTED_MATCH_MIN_CONFIDENCE", 0.85)
    )
    strict_auto_allow = bool(suggested_allow and expected_match and confidence >= threshold)

    if strict_auto_allow:
        _emit_status_note(
            status_note_callback,
            "UAC screenshot matches expected action with trusted signals. Sending ALLOW.",
        )
        return True

    _emit_status_note(
        status_note_callback,
        "UAC did not meet strict auto-allow policy. Requesting user confirmation.",
    )
    confirmation = confirm_uac_allow(
        chat_window=chat_window,
        reasoning=reasoning,
        action_name=action_name,
        expected_intent=expected,
        prompt_state=state,
        status_note_callback=status_note_callback,
        timeout_seconds=user_timeout_seconds,
    )

    if bool(confirmation.get("approved")):
        _emit_status_note(status_note_callback, "User approved UAC request. Sending ALLOW.")
        return True

    if bool(confirmation.get("timed_out")):
        _emit_status_note(status_note_callback, "No user confirmation received. Sending DENY.")
    else:
        _emit_status_note(status_note_callback, "UAC confirmation denied. Sending DENY.")
    return False


__all__ = [
    "ask_uac_brain",
    "confirm_uac_allow",
    "resolve_uac_allow_decision",
]
