from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from config import Config
from uac.detection import get_uac_prompt_state
from uac.ipc import create_request, write_response


logger = logging.getLogger("pixelpilot.uac.flow")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_FLOW_LOCK = threading.RLock()
_FLOW_STATE: dict[str, Any] = {
    "active": False,
    "flow_id": "",
    "status": "idle",
    "message": "No active UAC flow.",
    "action": "",
    "decision": "",
    "attempts": 0,
    "prompt": {},
    "started_at": None,
    "updated_at": None,
    "resolved_at": None,
    "last_error": "",
}

_EXTERNAL_UAC_MODE: dict[str, Any] = {
    "active": False,
    "source": "",
    "message": "UAC mode inactive.",
    "prompt": {},
    "updated_at": None,
}


def _copy_flow_state() -> dict[str, Any]:
    with _FLOW_LOCK:
        state = dict(_FLOW_STATE)
        state["prompt"] = dict(state.get("prompt") or {})
        return state


def _copy_external_mode_state() -> dict[str, Any]:
    with _FLOW_LOCK:
        state = dict(_EXTERNAL_UAC_MODE)
        state["prompt"] = dict(state.get("prompt") or {})
        return state


def _set_flow_state(**updates: Any) -> dict[str, Any]:
    with _FLOW_LOCK:
        _FLOW_STATE.update(updates)
        _FLOW_STATE["updated_at"] = _utc_now_iso()
        state = dict(_FLOW_STATE)
        state["prompt"] = dict(state.get("prompt") or {})
        return state


def get_uac_flow_progress() -> dict[str, Any]:
    flow_state = _copy_flow_state()
    flow_state["external_mode"] = _copy_external_mode_state()
    flow_state["queue_gate"] = get_uac_queue_gate()
    return flow_state


def get_external_uac_mode() -> dict[str, Any]:
    return _copy_external_mode_state()


def set_external_uac_mode(
    active: bool,
    *,
    source: str = "external_detector",
    message: str = "",
    prompt: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clean_source = str(source or "external_detector").strip() or "external_detector"
    clean_message = str(message or "").strip()
    is_active = bool(active)

    if not clean_message:
        clean_message = (
            "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."
            if is_active
            else "UAC mode cleared. Resuming queued actions."
        )

    now = _utc_now_iso()
    prompt_payload: dict[str, Any] = {}
    if isinstance(prompt, dict):
        prompt_payload = dict(prompt)

    with _FLOW_LOCK:
        if prompt_payload:
            next_prompt = prompt_payload
        elif is_active:
            next_prompt = dict(_EXTERNAL_UAC_MODE.get("prompt") or {})
        else:
            next_prompt = {}

        previous_active = bool(_EXTERNAL_UAC_MODE.get("active"))
        _EXTERNAL_UAC_MODE.update(
            {
                "active": is_active,
                "source": clean_source,
                "message": clean_message,
                "prompt": next_prompt,
                "updated_at": now,
            }
        )

    if previous_active != is_active:
        logger.info(
            "UAC_MODE_SET active=%s source=%s message=%s",
            is_active,
            clean_source,
            clean_message,
        )
    else:
        logger.debug(
            "UAC_MODE_REFRESH active=%s source=%s message=%s",
            is_active,
            clean_source,
            clean_message,
        )

    return _copy_external_mode_state()


def get_uac_queue_gate() -> dict[str, Any]:
    flow_state = _copy_flow_state()
    external_state = _copy_external_mode_state()

    flow_active = bool(flow_state.get("active"))
    external_active = bool(external_state.get("active"))
    active = bool(flow_active or external_active)

    if external_active:
        message = str(
            external_state.get("message")
            or "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."
        ).strip()
        source = str(external_state.get("source") or "external_detector").strip() or "external_detector"
        prompt = dict(external_state.get("prompt") or {})
    elif flow_active:
        message = str(
            flow_state.get("message")
            or "UAC workflow active. Waiting for prompt resolution."
        ).strip()
        source = "uac_flow"
        prompt = dict(flow_state.get("prompt") or {})
    else:
        message = "UAC mode inactive."
        source = ""
        prompt = {}

    return {
        "active": active,
        "message": message,
        "source": source,
        "flow_active": flow_active,
        "external_active": external_active,
        "prompt": prompt,
        "updated_at": _utc_now_iso(),
    }


def get_uac_poll_interval_seconds() -> float:
    return max(0.1, float(getattr(Config, "UAC_IPC_POLL_INTERVAL_SECONDS", 0.5) or 0.5))


def wait_for_uac_mode_clear(
    *,
    timeout_seconds: float,
    on_wait: Optional[Callable[[str], None]] = None,
    on_cleared: Optional[Callable[[str], None]] = None,
) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 1.0))
    wait_notified = False
    poll_interval_s = get_uac_poll_interval_seconds()

    while time.monotonic() < deadline:
        gate = get_uac_queue_gate()
        if not bool(gate.get("active")):
            if wait_notified and on_cleared is not None:
                try:
                    on_cleared("UAC MODE CLEARED: Resuming capture flow.")
                except Exception:
                    logger.debug("Failed to emit UAC clear callback", exc_info=True)
            return True

        if not wait_notified and on_wait is not None:
            wait_message = str(gate.get("message") or "").strip()
            if not wait_message:
                wait_message = "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."
            try:
                on_wait(f"UAC MODE ACTIVE: {wait_message}")
            except Exception:
                logger.debug("Failed to emit UAC waiting callback", exc_info=True)
            wait_notified = True

        time.sleep(poll_interval_s)

    return False


def _notify(
    *,
    status_note_callback: Optional[Callable[[str], None]],
    progress_callback: Optional[Callable[[dict[str, Any]], None]],
    state: dict[str, Any],
) -> None:
    message = str(state.get("message") or "").strip()
    if status_note_callback and message:
        try:
            status_note_callback(message)
        except Exception:
            pass
    if progress_callback:
        try:
            progress_callback(dict(state))
        except Exception:
            pass


def _truncate_text(value: str, *, max_len: int = 600) -> str:
    clean = str(value or "").strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max(0, max_len - 3)] + "..."


def _get_orchestrator_log_path() -> Path | None:
    try:
        from uac.ipc import ensure_ipc_root

        return ensure_ipc_root() / "orchestrator.log"
    except Exception:
        return None


def _read_orchestrator_log_tail(*, max_lines: int = 8) -> str:
    log_path = _get_orchestrator_log_path()
    if log_path is None or not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        logger.debug("Failed to read orchestrator log tail from %s", log_path, exc_info=True)
        return ""

    tail = [line.strip() for line in lines[-max(1, int(max_lines or 1)) :] if line.strip()]
    if not tail:
        return ""
    return " | ".join(_truncate_text(line, max_len=220) for line in tail)


def _wait_for_uac_snapshot(
    snapshot_path: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> bool:
    clean_path = str(snapshot_path or "").strip()
    if not clean_path:
        return False

    snapshot_file = Path(clean_path)
    deadline = time.monotonic() + max(0.2, float(timeout_seconds or 0.2))
    poll_s = max(0.05, float(poll_interval_seconds or 0.05))

    while time.monotonic() < deadline:
        try:
            if snapshot_file.exists() and snapshot_file.stat().st_size > 0:
                return True
        except Exception:
            logger.debug("Failed to inspect UAC snapshot path %s", snapshot_file, exc_info=True)
        time.sleep(poll_s)

    return False


def _dispatch_decision_via_api(decision: str) -> tuple[bool, str]:
    host = str(getattr(Config, "UAC_ORCHESTRATOR_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(getattr(Config, "UAC_ORCHESTRATOR_API_PORT", 8779) or 8779)
    timeout_s = float(getattr(Config, "UAC_ORCHESTRATOR_API_TIMEOUT_SECONDS", 3.0) or 3.0)
    url = f"http://{host}:{port}/uac/decision"

    payload = json.dumps({"decision": decision}, ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    logger.info(
        "UAC_DECISION_API_REQUEST decision=%s url=%s timeout_s=%.2f",
        decision,
        url,
        timeout_s,
    )
    started = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        parsed = json.loads(body) if body else {}
        if bool(parsed.get("ok")):
            logger.info(
                "UAC_DECISION_API_RESPONSE decision=%s status=%s elapsed_ms=%s ok=true",
                decision,
                status_code,
                elapsed_ms,
            )
            return True, "Decision submitted to orchestrator API."
        error_message = str(parsed.get("error") or "unknown_response").strip() or "unknown_response"
        logger.warning(
            "UAC_DECISION_API_RESPONSE decision=%s status=%s elapsed_ms=%s ok=false error=%s payload=%s",
            decision,
            status_code,
            elapsed_ms,
            error_message,
            _truncate_text(json.dumps(parsed, ensure_ascii=True), max_len=800),
        )
        return False, f"Orchestrator API rejected request: {error_message}"
    except TimeoutError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "UAC_DECISION_API_TIMEOUT decision=%s url=%s elapsed_ms=%s error=%s",
            decision,
            url,
            elapsed_ms,
            exc,
        )
        return False, f"Orchestrator API timed out after {elapsed_ms} ms: {exc}"
    except urllib.error.URLError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "UAC_DECISION_API_UNAVAILABLE decision=%s url=%s elapsed_ms=%s error=%s",
            decision,
            url,
            elapsed_ms,
            exc,
        )
        return False, f"Orchestrator API unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "UAC_DECISION_API_FAILED decision=%s url=%s elapsed_ms=%s error=%s",
            decision,
            url,
            elapsed_ms,
            exc,
        )
        return False, f"Orchestrator API call failed: {exc}"


def _dispatch_decision_local_fallback(decision: str) -> tuple[bool, str]:
    logger.info("UAC_DECISION_LOCAL_FALLBACK_START decision=%s", decision)
    try:
        from uac.orchestrator import dispatch_decision_to_secure_desktop
    except Exception as exc:  # noqa: BLE001
        logger.warning("UAC_DECISION_LOCAL_FALLBACK_IMPORT_FAILED decision=%s error=%s", decision, exc)
        return False, f"Local orchestrator fallback unavailable: {exc}"

    try:
        ok = bool(dispatch_decision_to_secure_desktop(decision))
        if ok:
            logger.info("UAC_DECISION_LOCAL_FALLBACK_RESULT decision=%s ok=true", decision)
            return True, "Decision submitted via local orchestrator fallback."

        log_path = _get_orchestrator_log_path()
        tail = _read_orchestrator_log_tail(max_lines=10)
        detail = "Local orchestrator fallback rejected decision dispatch."
        if log_path is not None:
            detail = f"{detail} log_path={log_path}"
        if tail:
            detail = f"{detail} tail={tail}"

        logger.warning(
            "UAC_DECISION_LOCAL_FALLBACK_RESULT decision=%s ok=false detail=%s",
            decision,
            _truncate_text(detail, max_len=1000),
        )
        return False, detail
    except Exception as exc:  # noqa: BLE001
        logger.warning("UAC_DECISION_LOCAL_FALLBACK_FAILED decision=%s error=%s", decision, exc)
        return False, f"Local orchestrator fallback failed: {exc}"


def submit_uac_decision(decision: str) -> tuple[bool, str]:
    clean_decision = str(decision or "").strip().upper()
    if clean_decision not in {"ALLOW", "DENY"}:
        return False, "Decision must be ALLOW or DENY."

    logger.info("UAC_DECISION_SUBMIT_START decision=%s", clean_decision)

    api_ok, api_message = _dispatch_decision_via_api(clean_decision)
    if api_ok:
        logger.info(
            "UAC_DECISION_SUBMIT_SUCCESS decision=%s path=api detail=%s",
            clean_decision,
            _truncate_text(api_message, max_len=500),
        )
        return True, api_message

    logger.warning(
        "UAC_DECISION_SUBMIT_API_FAILED decision=%s detail=%s",
        clean_decision,
        _truncate_text(api_message, max_len=800),
    )

    fallback_ok, fallback_message = _dispatch_decision_local_fallback(clean_decision)
    if fallback_ok:
        logger.info(
            "UAC_DECISION_SUBMIT_SUCCESS decision=%s path=local_fallback detail=%s",
            clean_decision,
            _truncate_text(fallback_message, max_len=500),
        )
        return True, fallback_message

    combined_error = f"{api_message}; {fallback_message}"
    logger.error(
        "UAC_DECISION_SUBMIT_FAILED decision=%s api_detail=%s fallback_detail=%s",
        clean_decision,
        _truncate_text(api_message, max_len=800),
        _truncate_text(fallback_message, max_len=800),
    )
    return False, combined_error


def handle_uac_prompt_blocking(
    *,
    action_label: str,
    ask_confirmation: Optional[Callable[[dict[str, Any]], bool]] = None,
    status_note_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_event: Any = None,
    poll_interval_seconds: Optional[float] = None,
    clear_timeout_seconds: Optional[float] = None,
) -> dict[str, Any]:
    interval_s = max(0.1, float(poll_interval_seconds or Config.UAC_IPC_POLL_INTERVAL_SECONDS or 0.5))
    timeout_s = max(1.0, float(clear_timeout_seconds or getattr(Config, "UAC_PROMPT_CLEAR_TIMEOUT_SECONDS", 20.0)))
    label = str(action_label or "desktop action").strip() or "desktop action"

    initial_prompt = get_uac_prompt_state()
    if not bool(initial_prompt.get("likelyPromptActive")):
        state = _set_flow_state(
            active=False,
            status="clear",
            message="No active UAC prompt.",
            action=label,
            decision="",
            prompt=initial_prompt,
            last_error="",
            resolved_at=_utc_now_iso(),
        )
        return {
            "handled": False,
            "success": True,
            "status": "clear",
            "message": "No active UAC prompt.",
            "decision": "",
            "prompt": initial_prompt,
            "progress": state,
        }

    logger.info(
        "UAC_FLOW_DETECTED action=%s confidence=%s secure_desktop=%s consent_pids=%s",
        label,
        str(initial_prompt.get("confidence") or ""),
        bool(initial_prompt.get("secureDesktopActive")),
        initial_prompt.get("consentProcessPids"),
    )

    flow_id = str(uuid.uuid4())
    state = _set_flow_state(
        active=True,
        flow_id=flow_id,
        status="detected",
        message=f"UAC prompt detected while running {label}. Waiting for approval.",
        action=label,
        decision="",
        attempts=0,
        prompt=initial_prompt,
        started_at=_utc_now_iso(),
        resolved_at=None,
        last_error="",
    )
    _notify(
        status_note_callback=status_note_callback,
        progress_callback=progress_callback,
        state=state,
    )

    if cancel_event is not None:
        try:
            if bool(cancel_event.is_set()):
                cancelled_state = _set_flow_state(
                    active=False,
                    status="cancelled",
                    message="UAC handling cancelled before decision.",
                    last_error="cancelled",
                    resolved_at=_utc_now_iso(),
                )
                _notify(
                    status_note_callback=status_note_callback,
                    progress_callback=progress_callback,
                    state=cancelled_state,
                )
                return {
                    "handled": True,
                    "success": False,
                    "status": "cancelled",
                    "message": "UAC handling cancelled.",
                    "decision": "",
                    "prompt": initial_prompt,
                    "progress": cancelled_state,
                }
        except Exception:
            pass

    poll_interval_for_snapshot_s = max(
        0.05,
        float(poll_interval_seconds or Config.UAC_IPC_POLL_INTERVAL_SECONDS or 0.5),
    )
    configured_snapshot_wait_s = float(
        getattr(Config, "UAC_REQUEST_SNAPSHOT_WAIT_SECONDS", 22.0) or 22.0
    )
    startup_budget_s = (
        float(getattr(Config, "UAC_RESPONSE_TIMEOUT_SECONDS", 15.0) or 15.0)
        + float(getattr(Config, "UAC_HELPER_INITIAL_CAPTURE_DELAY_SECONDS", 1.0) or 1.0)
        + 4.0
    )
    snapshot_wait_s = max(0.5, configured_snapshot_wait_s, startup_budget_s)

    allow = False
    allow_resolved = False
    decision = "DENY"
    dispatch_message = ""
    submitted_via_request = False
    request_error = ""

    try:
        request_payload = create_request()
        request_nonce = str(request_payload.get("nonce") or "").strip()
        snapshot_path = str(request_payload.get("snapshot_path") or "").strip()

        logger.info(
            "UAC_REQUEST_CREATED action=%s nonce=%s snapshot_path=%s",
            label,
            request_nonce,
            snapshot_path,
        )

        capture_state = _set_flow_state(
            status="capturing_snapshot",
            message="Capturing secure desktop prompt for AI review.",
            prompt=initial_prompt,
        )
        _notify(
            status_note_callback=status_note_callback,
            progress_callback=progress_callback,
            state=capture_state,
        )

        snapshot_ready = _wait_for_uac_snapshot(
            snapshot_path,
            timeout_seconds=snapshot_wait_s,
            poll_interval_seconds=poll_interval_for_snapshot_s,
        )
        if snapshot_ready:
            logger.info(
                "UAC_REQUEST_SNAPSHOT_READY action=%s nonce=%s path=%s",
                label,
                request_nonce,
                snapshot_path,
            )
            prompt_with_snapshot = dict(initial_prompt)
            prompt_with_snapshot.update(
                {
                    "uac_snapshot_path": snapshot_path,
                    "uac_request_nonce": request_nonce,
                    "uac_request_path": str(request_payload.get("request_path") or ""),
                    "uac_response_path": str(request_payload.get("response_path") or ""),
                }
            )

            if ask_confirmation is not None:
                try:
                    allow = bool(ask_confirmation(dict(prompt_with_snapshot)))
                except Exception:
                    allow = False
            allow_resolved = True
            decision = "ALLOW" if allow else "DENY"

            logger.info(
                "UAC_FLOW_DECISION action=%s decision=%s via=request_snapshot",
                label,
                decision,
            )
            dispatch_state = _set_flow_state(
                status="dispatching",
                message=f"Submitting {decision} decision to secure desktop helper.",
                decision=decision,
                attempts=int(_FLOW_STATE.get("attempts", 0) or 0) + 1,
            )
            _notify(
                status_note_callback=status_note_callback,
                progress_callback=progress_callback,
                state=dispatch_state,
            )

            write_response(
                request_payload,
                allow=allow,
                user_confirmed=bool(allow),
                reasoning=f"Flow decision {decision} for {label}.",
            )
            logger.info(
                "UAC_REQUEST_RESPONSE_WRITTEN action=%s nonce=%s decision=%s",
                label,
                request_nonce,
                decision,
            )
            submitted_via_request = True
            dispatch_message = "Decision submitted via secure desktop request helper."
        else:
            request_error = (
                f"UAC request snapshot not ready within {snapshot_wait_s:.2f}s. "
                "Falling back to direct decision dispatch."
            )
            logger.warning(
                "UAC_REQUEST_SNAPSHOT_TIMEOUT action=%s nonce=%s timeout_s=%.2f configured_wait_s=%.2f startup_budget_s=%.2f",
                label,
                request_nonce,
                snapshot_wait_s,
                configured_snapshot_wait_s,
                startup_budget_s,
            )
    except Exception as exc:  # noqa: BLE001
        request_error = f"UAC request mode failed: {exc}"
        logger.warning("UAC_REQUEST_MODE_FAILED action=%s error=%s", label, exc)

    if not submitted_via_request:
        if request_error:
            logger.info(
                "UAC_REQUEST_MODE_FALLBACK action=%s detail=%s",
                label,
                _truncate_text(request_error, max_len=900),
            )

        if not allow_resolved and ask_confirmation is not None:
            try:
                allow = bool(ask_confirmation(dict(initial_prompt)))
            except Exception:
                allow = False

        decision = "ALLOW" if allow else "DENY"
        logger.info("UAC_FLOW_DECISION action=%s decision=%s via=direct_dispatch", label, decision)

        dispatch_state = _set_flow_state(
            status="dispatching",
            message=f"Submitting {decision} decision to secure desktop helper.",
            decision=decision,
            attempts=int(_FLOW_STATE.get("attempts", 0) or 0) + 1,
        )
        _notify(
            status_note_callback=status_note_callback,
            progress_callback=progress_callback,
            state=dispatch_state,
        )

        dispatched, dispatch_message = submit_uac_decision(decision)
        if not dispatched:
            logger.warning(
                "UAC_FLOW_DISPATCH_FAILED action=%s decision=%s detail=%s",
                label,
                decision,
                _truncate_text(dispatch_message, max_len=1200),
            )
            failed_state = _set_flow_state(
                active=False,
                status="dispatch_failed",
                message="Could not send UAC decision to secure desktop helper.",
                last_error=dispatch_message,
                resolved_at=_utc_now_iso(),
            )
            _notify(
                status_note_callback=status_note_callback,
                progress_callback=progress_callback,
                state=failed_state,
            )
            return {
                "handled": True,
                "success": False,
                "status": "dispatch_failed",
                "message": failed_state.get("message") or "Could not send UAC decision.",
                "decision": decision,
                "prompt": initial_prompt,
                "dispatch_detail": dispatch_message,
                "progress": failed_state,
            }

    submitted_message = (
        "Decision submitted via secure desktop request helper. Waiting for UAC prompt to close."
        if submitted_via_request
        else "Decision submitted. Waiting for UAC prompt to close."
    )
    submitted_state = _set_flow_state(
        status="submitted",
        message=submitted_message,
        last_error="",
    )
    _notify(
        status_note_callback=status_note_callback,
        progress_callback=progress_callback,
        state=submitted_state,
    )

    deadline = time.monotonic() + timeout_s
    while True:
        if cancel_event is not None:
            try:
                if bool(cancel_event.is_set()):
                    cancelled_state = _set_flow_state(
                        active=False,
                        status="cancelled",
                        message="UAC handling cancelled while waiting for completion.",
                        last_error="cancelled",
                        resolved_at=_utc_now_iso(),
                    )
                    _notify(
                        status_note_callback=status_note_callback,
                        progress_callback=progress_callback,
                        state=cancelled_state,
                    )
                    return {
                        "handled": True,
                        "success": False,
                        "status": "cancelled",
                        "message": cancelled_state.get("message") or "UAC handling cancelled.",
                        "decision": decision,
                        "prompt": get_uac_prompt_state(),
                        "progress": cancelled_state,
                    }
            except Exception:
                pass

        prompt_state = get_uac_prompt_state()
        active = bool(prompt_state.get("likelyPromptActive"))
        if not active:
            if decision == "ALLOW":
                final_status = "resolved_allowed"
                final_message = f"UAC prompt handled for {label}. Resuming task."
                success = True
            else:
                final_status = "resolved_denied"
                final_message = "UAC request denied. Desktop action was not executed."
                success = False

            resolved_state = _set_flow_state(
                active=False,
                status=final_status,
                message=final_message,
                prompt=prompt_state,
                resolved_at=_utc_now_iso(),
                last_error="",
            )
            logger.info(
                "UAC_FLOW_RESOLVED action=%s decision=%s status=%s",
                label,
                decision,
                final_status,
            )
            _notify(
                status_note_callback=status_note_callback,
                progress_callback=progress_callback,
                state=resolved_state,
            )
            return {
                "handled": True,
                "success": success,
                "status": final_status,
                "message": final_message,
                "decision": decision,
                "prompt": prompt_state,
                "progress": resolved_state,
            }

        if time.monotonic() >= deadline:
            logger.warning(
                "UAC_FLOW_TIMEOUT action=%s decision=%s timeout_s=%.2f",
                label,
                decision,
                timeout_s,
            )
            timeout_state = _set_flow_state(
                active=False,
                status="timeout",
                message="Timed out waiting for UAC prompt to close.",
                prompt=prompt_state,
                last_error="timeout",
                resolved_at=_utc_now_iso(),
            )
            _notify(
                status_note_callback=status_note_callback,
                progress_callback=progress_callback,
                state=timeout_state,
            )
            return {
                "handled": True,
                "success": False,
                "status": "timeout",
                "message": timeout_state.get("message") or "Timed out waiting for UAC prompt.",
                "decision": decision,
                "prompt": prompt_state,
                "progress": timeout_state,
            }

        waiting_state = _set_flow_state(
            status="waiting_clear",
            message="Waiting for UAC prompt to finish...",
            prompt=prompt_state,
        )
        _notify(
            status_note_callback=status_note_callback,
            progress_callback=progress_callback,
            state=waiting_state,
        )
        time.sleep(interval_s)


__all__ = [
    "get_uac_poll_interval_seconds",
    "get_external_uac_mode",
    "get_uac_flow_progress",
    "get_uac_queue_gate",
    "handle_uac_prompt_blocking",
    "set_external_uac_mode",
    "submit_uac_decision",
    "wait_for_uac_mode_clear",
]
