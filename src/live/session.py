from __future__ import annotations

import audioop
import asyncio
import datetime as dt
import io
import json
import logging
import math
import re
import struct
import threading
import time
from collections import deque
from collections.abc import Coroutine
from typing import Any, Optional

import pyaudio
from PIL import Image
from PySide6.QtCore import QObject, Signal

from backend_client import RateLimitError
from agent.prompts import (
    LIVE_GUIDANCE_SYSTEM_INSTRUCTION,
    LIVE_SYSTEM_CONTEXT_PREFIX,
    LIVE_SYSTEM_INSTRUCTION,
)
from config import Config, OperationMode
from uac.detection import get_uac_prompt_state
from uac.flow import get_uac_queue_gate, set_external_uac_mode
from .broker import LiveActionBroker
from .transports import BaseLiveTransport
from .tools import LiveToolRegistry
from tools import ui_automation
from model_providers import get_live_provider_config

logger = logging.getLogger("pixelpilot.live.session")

try:
    from google import genai
    from google.genai import types
except Exception as exc:  # noqa: BLE001
    genai = None
    types = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""

class LiveSessionManager(QObject):
    transcript_received = Signal(str, str, bool)
    session_state_changed = Signal(str)
    action_state_changed = Signal(object)
    error_received = Signal(str)
    status_received = Signal(str)
    audio_level_changed = Signal(float)
    assistant_audio_level_changed = Signal(float)
    availability_changed = Signal(bool, str)
    voice_active_changed = Signal(bool)

    def __init__(self, *, agent) -> None:
        super().__init__()
        self.agent = agent
        self.enabled = False
        self._voice_enabled = False
        self._workspace = getattr(agent, "active_workspace", "user")
        self._mode = getattr(agent, "mode", None)
        self._transport: Optional[BaseLiveTransport] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._connect_in_progress = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._session_started_at = 0.0
        self._resume_handle: Optional[str] = None
        self._resume_pending_user_buffer = ""
        self._resume_pending_assistant_buffer = ""
        self._speaker_queue: Optional[asyncio.Queue[tuple[bytes, int]]] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._video_task: Optional[asyncio.Task] = None
        self._speaker_task: Optional[asyncio.Task] = None
        self._mic_task: Optional[asyncio.Task] = None
        self._rotation_task: Optional[asyncio.Task] = None
        self._guidance_observer_task: Optional[asyncio.Task] = None
        self._uac_watchdog_task: Optional[asyncio.Task] = None
        self._one_shot_timeout_task: Optional[asyncio.Task] = None
        self._one_shot_finalize_task: Optional[asyncio.Task] = None
        self._go_away_reconnect_task: Optional[asyncio.Task] = None
        self._idle_disconnect_task: Optional[asyncio.Task] = None
        self._disconnect_after_reply_task: Optional[asyncio.Task] = None
        self._shutdown_event = threading.Event()
        self._assistant_buffer = ""
        self._user_buffer = ""
        self._current_goal = ""
        self._recent_user_steering: deque[str] = deque(maxlen=6)
        self._recent_action_updates: deque[dict[str, Any]] = deque(maxlen=12)
        self._pending_capture_paths: deque[tuple[str, dict[str, Any]]] = deque(maxlen=4)
        self._audio_output_suppressed_until = 0.0
        self._reconnect_in_progress = False
        self._last_invalid_request_recovery_at = 0.0
        provider_config = get_live_provider_config()
        self._provider_config = provider_config
        self._voice_supported = bool(provider_config.capabilities.audio_input and provider_config.capabilities.audio_output)
        self._image_input_enabled = bool(Config.LIVE_ENABLE_IMAGE_INPUT and provider_config.capabilities.image_input)
        self._video_stream_enabled = bool(
            Config.LIVE_ENABLE_VIDEO_STREAM
            and self._image_input_enabled
            and provider_config.capabilities.video_input
        )
        self._speaker_drop_logged_at = 0.0
        self._speaker_backlog_logged_at = 0.0
        self._speaker_backpressure_logged_at = 0.0
        self._audio_resample_logged_at = 0.0
        self._live_rate_limited_until_monotonic = 0.0
        self._live_rate_limit_message = ""
        self._last_guidance_snapshot_signature = ""
        self._last_guidance_probe_sent_at = 0.0
        self._turn_state_lock = threading.Lock()
        self._active_text_turn_id: Optional[int] = None
        self._next_text_turn_id = 0
        self._turn_waiters: dict[int, dict[str, Any]] = {}
        self._last_typed_turn_activity_at = 0.0
        self._last_live_activity_at = time.monotonic()
        self._typed_turn_idle_finish_timer: Optional[threading.Timer] = None
        self._typed_turn_idle_finish_generation = 0
        self._pending_text_nudge = ""
        self._pending_text_nudge_timer: Optional[threading.Timer] = None
        self._pending_text_nudge_generation = 0
        self._pending_text_commands: deque[str] = deque(maxlen=8)
        self._pending_text_command_flush_in_progress = False
        self._soft_interrupt_requested = False
        self._manual_disconnect_requested = False
        self._pending_disconnect_status_message = ""
        self._voice_mode = "continuous"
        self._one_shot_engaged = False
        self._baseline_live_thinking_level = self._normalize_thinking_level(
            Config.LIVE_THINKING_LEVEL
        )
        self._thinking_level_override = ""
        self._last_successful_reasoning_escalation_level = ""
        self._runtime_uac_mode_active = False

        self.broker = LiveActionBroker(
            on_action_update=self._on_action_update,
            wait_gate=self._uac_action_queue_gate,
            on_waiting=self._on_action_waiting_note,
        )
        self.tools = LiveToolRegistry(
            agent=agent,
            broker=self.broker,
            on_capture_ready=self._on_capture_ready,
            on_disconnect_requested=self._request_live_disconnect,
            on_reasoning_escalation=self._request_reasoning_escalation,
            on_status_note=lambda message: self.status_received.emit(str(message)),
        )
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self.availability_changed.emit(self.is_available, self.unavailable_reason)

    def _session_store_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        store = getattr(self.agent, "session_store", None)
        if store is None:
            return None
        method = getattr(store, method_name, None)
        if not callable(method):
            return None
        try:
            return method(*args, **kwargs)
        except Exception:
            logger.debug("Session store call failed: %s", method_name, exc_info=True)
            return None

    def _record_resume_metadata(self) -> None:
        self._session_store_call("record_resume_metadata", self._build_resume_summary())

    def _mode_key(self, mode: Optional[object] = None) -> str:
        value = self._mode if mode is None else mode
        if isinstance(value, OperationMode):
            return value.value
        enum_value = getattr(value, "value", value)
        return str(enum_value or "").strip().lower()

    @staticmethod
    def _uac_action_queue_gate() -> dict[str, Any]:
        gate = get_uac_queue_gate()
        if bool(gate.get("active")) and not str(gate.get("message") or "").strip():
            gate["message"] = "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."
        return gate

    def _on_action_waiting_note(self, message: str) -> None:
        clean = str(message or "").strip()
        if clean:
            self.status_received.emit(clean)

    def _queue_uac_runtime_hint_to_model(self, *, active: bool, message: str = "") -> None:
        if not self.enabled or self._transport is None or self._shutdown_event.is_set():
            return
        if self._reconnect_in_progress:
            return

        status_text = str(message or "").strip()
        if bool(active):
            hint = (
                "Runtime UAC update. This is internal state, not a new user request. "
                "UAC mode is active and elevation is pending. Do not claim an administrator "
                "launch succeeded yet. Wait for UAC result confirmation (ALLOW or DENY) "
                "from uac_get_progress or runtime status updates before reporting outcome."
            )
        else:
            hint = (
                "Runtime UAC update. This is internal state, not a new user request. "
                "UAC mode is cleared. Report elevation outcome strictly from the final UAC "
                "result, and only claim administrator success when the resolved decision is ALLOW."
            )

        if status_text:
            hint = f"{hint} Runtime status: {json.dumps(status_text, ensure_ascii=True)}"

        self._submit_async(
            self._send_realtime_text(hint, allow_retry=False),
            ensure_loop=False,
        )

    @staticmethod
    def _looks_like_admin_completion_claim(text: str) -> bool:
        clean = str(text or "").strip().lower()
        if not clean:
            return False

        if any(
            marker in clean
            for marker in (
                "can't",
                "cannot",
                "unable",
                "won't",
                "will not",
                "didn't",
                "did not",
                "pending",
                "waiting",
                "not yet",
                "denied",
            )
        ):
            return False

        completion_markers = (
            "opened",
            "launched",
            "started",
            "completed",
            "done",
            "successful",
            "succeeded",
            "granted",
            "approved",
        )
        admin_markers = (
            "administrator",
            "as admin",
            "admin mode",
            "elevat",
            "uac",
        )
        return any(word in clean for word in completion_markers) and any(
            word in clean for word in admin_markers
        )

    def _guard_assistant_output_for_uac(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return clean
        if not bool(self._runtime_uac_mode_active):
            return clean
        if not self._looks_like_admin_completion_claim(clean):
            return clean

        logger.info(
            "LIVE_UAC_RESPONSE_GUARD original=%s",
            self._truncate_log_text(clean),
        )
        return (
            "UAC approval is still pending. I will confirm administrator launch status only "
            "after UAC resolves with ALLOW or DENY."
        )

    def _set_runtime_uac_mode(
        self,
        active: bool,
        *,
        source: str,
        message: str = "",
        prompt_state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        is_active = bool(active)
        prompt_payload = dict(prompt_state or {}) if isinstance(prompt_state, dict) else None
        state = set_external_uac_mode(
            is_active,
            source=str(source or "live_session").strip() or "live_session",
            message=str(message or ""),
            prompt=prompt_payload,
        )
        previous = bool(self._runtime_uac_mode_active)
        self._runtime_uac_mode_active = is_active
        if previous != is_active:
            logger.info(
                "LIVE_UAC_MODE_SET active=%s source=%s message=%s",
                is_active,
                str(source or "live_session"),
                self._truncate_log_text(state.get("message")),
            )
            self._queue_uac_runtime_hint_to_model(
                active=is_active,
                message=str(state.get("message") or message or ""),
            )
        return state

    def _is_guidance_mode(self, mode: Optional[object] = None) -> bool:
        return self._mode_key(mode) == OperationMode.GUIDE.value

    def _mode_instruction_suffix(self) -> str:
        mode_key = self._mode_key()
        if mode_key == OperationMode.SAFE.value:
            return (
                "SAFE mode is active. Every mutating desktop action requires user confirmation. "
                "If a tool call is rejected, explain briefly and choose a safer next step."
            )
        if mode_key == OperationMode.AUTO.value:
            return "AUTO mode is active. Mutating desktop actions may proceed without per-action confirmation."
        return ""

    @staticmethod
    def _normalize_thinking_level(level: Any) -> str:
        clean = str(level or "").strip().lower()
        return clean if clean in {"minimal", "low", "medium", "high"} else ""

    @staticmethod
    def _thinking_level_rank(level: Any) -> int:
        return {
            "minimal": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }.get(LiveSessionManager._normalize_thinking_level(level) or "minimal", 0)

    def _baseline_thinking_level(self) -> str:
        self._baseline_live_thinking_level = self._normalize_thinking_level(
            Config.LIVE_THINKING_LEVEL
        )
        return self._baseline_live_thinking_level

    def _effective_thinking_level_for_compare(self) -> str:
        return (
            self._normalize_thinking_level(self._thinking_level_override)
            or self._baseline_thinking_level()
            or "minimal"
        )

    def _effective_thinking_level_for_config(self) -> str:
        return (
            self._normalize_thinking_level(self._thinking_level_override)
            or self._baseline_thinking_level()
        )

    def _reset_reasoning_escalation_state(self) -> None:
        self._baseline_live_thinking_level = self._normalize_thinking_level(
            Config.LIVE_THINKING_LEVEL
        )
        self._thinking_level_override = ""
        self._last_successful_reasoning_escalation_level = ""

    def _request_reasoning_escalation(
        self,
        target_level: str,
        reason: str,
    ) -> dict[str, Any]:
        clean_target = self._normalize_thinking_level(target_level)
        clean_reason = str(reason or "").strip()
        logger.info(
            "LIVE_REASONING_ESCALATION_REQUEST target_level=%s reason=%s",
            clean_target,
            self._truncate_log_text(clean_reason),
        )
        if clean_target not in {"medium", "high"}:
            return {
                "tool_name": "request_reasoning_escalation",
                "ok": False,
                "success": False,
                "status": "failed",
                "message": "target_level must be 'medium' or 'high'.",
                "result": None,
                "error": "invalid_args",
            }

        current_level = self._effective_thinking_level_for_compare()
        current_rank = self._thinking_level_rank(current_level)
        target_rank = self._thinking_level_rank(clean_target)
        reconnect_required = target_rank > current_rank
        effective_level = clean_target if reconnect_required else current_level

        if reconnect_required:
            self._thinking_level_override = clean_target
            self._last_successful_reasoning_escalation_level = clean_target

        result = {
            "requested_level": clean_target,
            "effective_level": effective_level,
            "previous_effective_level": current_level,
            "reconnect_required": reconnect_required,
        }
        if clean_reason:
            result["reason_recorded"] = True

        return {
            "tool_name": "request_reasoning_escalation",
            "ok": True,
            "success": True,
            "status": "succeeded",
            "message": (
                f"Reasoning escalation scheduled to {clean_target}."
                if reconnect_required
                else f"Reasoning level is already {current_level} or higher."
            ),
            "result": result,
            "error": None,
        }

    def _request_live_disconnect(self, reason: str) -> dict[str, Any]:
        clean_reason = str(reason or "").strip()
        phrase = str(Config.WAKE_WORD_PHRASE or "Hey Pixie").strip() or "Hey Pixie"
        status_message = (
            f'PixelPilot Live disconnected. Say "{phrase}" to reconnect if the wake word is enabled.'
            if Config.ENABLE_WAKE_WORD
            else "PixelPilot Live disconnected."
        )
        if clean_reason:
            status_message = f"{status_message} Reason: {clean_reason}"

        already_disconnected = not bool(self._transport or self._connect_task or self._voice_enabled)
        if already_disconnected:
            return {
                "tool_name": "disconnect_live_session",
                "ok": True,
                "success": True,
                "status": "succeeded",
                "message": status_message,
                "result": {
                    "disconnect_requested": False,
                    "status_message": status_message,
                    "wake_word_phrase": phrase,
                },
                "error": None,
            }

        return {
            "tool_name": "disconnect_live_session",
            "ok": True,
            "success": True,
            "status": "succeeded",
            "message": (
                "Disconnect queued. Finish with one short natural acknowledgement in your own words; "
                "the runtime will disconnect after this turn."
            ),
            "result": {
                "disconnect_requested": True,
                "reply_before_disconnect": True,
                "status_message": status_message,
                "wake_word_phrase": phrase,
            },
            "error": None,
        }

    def _transport_cls(self):
        self._provider_config = get_live_provider_config()
        from .transport_factory import resolve_transport_cls
        return resolve_transport_cls(
            self._provider_config.provider_id,
            self._provider_config.mode_kind,
        )

    def _create_transport(self) -> BaseLiveTransport:
        cls = self._transport_cls()
        if self._transport is not None and isinstance(self._transport, cls):
            return self._transport
        return cls()

    @property
    def is_connection_pending(self) -> bool:
        return bool(
            self.enabled
            and (
                self._connect_in_progress
                or self._reconnect_in_progress
                or self._connect_task is not None
                or self._transport is None
            )
        )

    @property
    def is_connected(self) -> bool:
        return bool(self._transport is not None and not self._reconnect_in_progress)

    @property
    def manual_disconnect_requested(self) -> bool:
        return bool(self._manual_disconnect_requested)

    @property
    def is_available(self) -> bool:
        transport_cls = self._transport_cls()
        return bool(transport_cls.is_supported())

    @property
    def unavailable_reason(self) -> str:
        transport_cls = self._transport_cls()
        return transport_cls.unavailable_reason()

    @property
    def voice_enabled(self) -> bool:
        return self._voice_enabled

    @property
    def voice_mode(self) -> str:
        return self._voice_mode

    def _should_auto_reconnect(self) -> bool:
        return bool(
            self.enabled
            and not self._shutdown_event.is_set()
            and not self._manual_disconnect_requested
        )

    def _clear_manual_disconnect_request(self) -> None:
        self._manual_disconnect_requested = False

    def _rate_limit_cooldown_seconds(self, exc: RateLimitError) -> int:
        retry_after = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            return max(1, min(24 * 60 * 60, int(math.ceil(float(retry_after)))))

        message = str(exc or "").strip().lower()
        if "midnight utc" in message or "daily time limit exceeded" in message:
            now_utc = dt.datetime.now(dt.timezone.utc)
            next_midnight = (now_utc + dt.timedelta(days=1)).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            seconds = int(math.ceil((next_midnight - now_utc).total_seconds()))
            return max(60, min(24 * 60 * 60, seconds))

        if "rate limit" in message:
            return 30
        return 20

    def _apply_rate_limit_cooldown(self, exc: RateLimitError, *, source: str) -> None:
        cooldown_s = self._rate_limit_cooldown_seconds(exc)
        now = time.monotonic()
        until = now + float(cooldown_s)
        self._live_rate_limited_until_monotonic = max(self._live_rate_limited_until_monotonic, until)
        self._live_rate_limit_message = str(exc or "Rate limit exceeded.").strip() or "Rate limit exceeded."
        logger.warning(
            "LIVE_RATE_LIMIT_COOLDOWN source=%s cooldown_seconds=%s message=%s",
            source,
            cooldown_s,
            self._truncate_log_text(self._live_rate_limit_message),
        )
        self.status_received.emit(self._live_rate_limit_message)

    def _active_rate_limit_error(self) -> Optional[RateLimitError]:
        now = time.monotonic()
        if now >= self._live_rate_limited_until_monotonic:
            self._live_rate_limited_until_monotonic = 0.0
            self._live_rate_limit_message = ""
            return None
        retry_after_seconds = max(1, int(math.ceil(self._live_rate_limited_until_monotonic - now)))
        message = self._live_rate_limit_message or "Rate limit exceeded."
        return RateLimitError(message, retry_after_seconds=retry_after_seconds)

    def _clear_resume_handle(self, *, reason: str = "") -> None:
        handle = str(self._resume_handle or "").strip()
        if not handle:
            return
        logger.warning(
            "Clearing Gemini Live session resumption handle%s.",
            f" ({reason})" if str(reason or "").strip() else "",
        )
        self._resume_handle = None

    @staticmethod
    def _truncate_log_text(text: Any, *, limit: int = 2400) -> str:
        clean = " ".join(str(text or "").split())
        if len(clean) <= limit:
            return clean
        return f"{clean[:limit]}...(truncated)"

    @classmethod
    def _serialize_log_value(cls, value: Any, *, limit: int = 2400) -> str:
        try:
            raw = json.dumps(value, ensure_ascii=True, default=str)
        except Exception:
            raw = repr(value)
        return cls._truncate_log_text(raw, limit=limit)

    def _log_user_request(self, text: str, *, source: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        logger.info(
            "LIVE_USER_REQUEST source=%s text=%s",
            str(source or "typed").strip().lower() or "typed",
            self._truncate_log_text(clean),
        )

    def _cancel_go_away_reconnect_task(self, *, keep_current: bool = False) -> None:
        task = self._go_away_reconnect_task
        if task is None:
            return
        if keep_current:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is current:
                return
        if not task.done():
            task.cancel()
        self._go_away_reconnect_task = None

    @staticmethod
    def _parse_go_away_time_left_seconds(raw_value: Any) -> Optional[float]:
        if raw_value is None:
            return None
        if isinstance(raw_value, (int, float)):
            return max(0.0, float(raw_value))

        text = str(raw_value or "").strip().lower()
        if not text:
            return None

        if ":" in text:
            try:
                parts = [float(item) for item in text.split(":")]
                if len(parts) == 3:
                    return max(0.0, (parts[0] * 3600.0) + (parts[1] * 60.0) + parts[2])
                if len(parts) == 2:
                    return max(0.0, (parts[0] * 60.0) + parts[1])
            except Exception:
                pass

        duration_match = re.fullmatch(
            r"(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+(?:\.\d+)?)s)?",
            text,
        )
        if duration_match and any(duration_match.groupdict().values()):
            hours = float(duration_match.group("hours") or 0.0)
            minutes = float(duration_match.group("minutes") or 0.0)
            seconds = float(duration_match.group("seconds") or 0.0)
            return max(0.0, (hours * 3600.0) + (minutes * 60.0) + seconds)

        try:
            return max(0.0, float(text))
        except Exception:
            return None

    def _schedule_go_away_reconnect(self, go_away_payload: dict[str, Any]) -> None:
        if not self._should_auto_reconnect() or self._reconnect_in_progress:
            return

        existing = self._go_away_reconnect_task
        if existing is not None and not existing.done():
            return

        time_left_s = self._parse_go_away_time_left_seconds(go_away_payload.get("time_left"))
        reconnect_deadline = None if time_left_s is None else (time.monotonic() + time_left_s)
        reconnect_delay_s = 0.0
        if time_left_s is not None:
            reconnect_delay_s = max(0.0, min(10.0, time_left_s - 1.5))

        async def _go_away_reconnect_loop() -> None:
            try:
                if reconnect_delay_s > 0.0:
                    await asyncio.sleep(reconnect_delay_s)

                while self.broker.has_pending():
                    if not self._should_auto_reconnect() or self._reconnect_in_progress:
                        return
                    if reconnect_deadline is not None and time.monotonic() >= reconnect_deadline:
                        return
                    await asyncio.sleep(0.25)

                if not self._should_auto_reconnect() or self._reconnect_in_progress:
                    return
                self.status_received.emit(
                    "Refreshing Gemini Live connection to keep the conversation active."
                )
                await self._reconnect_with_resume()
            except asyncio.CancelledError:
                return
            finally:
                current = asyncio.current_task()
                if self._go_away_reconnect_task is current:
                    self._go_away_reconnect_task = None

        self._go_away_reconnect_task = asyncio.create_task(_go_away_reconnect_loop())

    @staticmethod
    def _normalize_voice_mode(mode: Any) -> str:
        return "one_shot" if str(mode or "").strip().lower() == "one_shot" else "continuous"

    def _configure_voice_mode(self, mode: Any) -> None:
        self._voice_mode = self._normalize_voice_mode(mode)
        self._one_shot_engaged = False
        self._cancel_one_shot_tasks()

    def _cancel_one_shot_tasks(self, *, keep_current: bool = False) -> None:
        current_task = None
        if keep_current:
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None

        for attr in ("_one_shot_timeout_task", "_one_shot_finalize_task"):
            task = getattr(self, attr, None)
            if task is None:
                continue
            if keep_current and task is current_task:
                continue
            if not task.done():
                task.cancel()
            setattr(self, attr, None)

    def _schedule_one_shot_timeout(self) -> None:
        self._cancel_one_shot_tasks()
        if self._voice_mode != "one_shot" or not self._voice_enabled:
            return
        self._one_shot_timeout_task = asyncio.create_task(self._one_shot_timeout_loop())

    def _mark_one_shot_engaged(self) -> None:
        if self._voice_mode != "one_shot" or self._one_shot_engaged:
            return
        self._one_shot_engaged = True
        timeout_task = self._one_shot_timeout_task
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
        self._one_shot_timeout_task = None

    def _schedule_one_shot_finalize(self) -> None:
        if (
            self._voice_mode != "one_shot"
            or not self._voice_enabled
            or not self._one_shot_engaged
        ):
            return
        task = self._one_shot_finalize_task
        if task is not None and not task.done():
            return
        self._one_shot_finalize_task = asyncio.create_task(self._one_shot_finalize_loop())

    def set_enabled(self, enabled: bool) -> bool:
        target = bool(enabled)
        if target and not self.is_available:
            self.error_received.emit(self.unavailable_reason)
            return False
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self.enabled = target
        if target:
            self._clear_manual_disconnect_request()
        if not target:
            self._cancel_go_away_reconnect_task()
            self._cancel_idle_disconnect_task()
            self._cancel_disconnect_after_reply_task()
            self._cancel_one_shot_tasks()
            self.stop_voice()
            self._cancel_typed_turn_idle_finish_timer()
            self._clear_pending_text_commands()
            self._clear_pending_text_nudge()
            self._finish_text_turn(error="PixelPilot Live was disconnected.")
            self._reset_reasoning_escalation_state()
            self._submit_async(self._disconnect_session(close_client=True), ensure_loop=False)
            self.session_state_changed.emit("disconnected")
        return True

    def disconnect(self, *, reason: str = "") -> bool:
        if not self.enabled:
            return False

        self._manual_disconnect_requested = True
        logger.info(
            "LIVE_SESSION_DISCONNECT_REQUESTED reason=%s",
            self._truncate_log_text(reason),
        )
        self._cancel_go_away_reconnect_task()
        self._cancel_idle_disconnect_task()
        self._cancel_disconnect_after_reply_task()
        self.stop_voice()
        self._cancel_typed_turn_idle_finish_timer()
        self._clear_pending_text_commands()
        self._clear_pending_text_nudge()
        self._finish_text_turn(error="PixelPilot Live was disconnected.")
        self._reset_reasoning_escalation_state()

        submitted = self._submit_async(self._disconnect_session(close_client=True), ensure_loop=False)
        if not submitted:
            self.session_state_changed.emit("disconnected")
        if str(reason or "").strip():
            self.status_received.emit(str(reason))
        return True

    def reconnect(self) -> bool:
        if not self.enabled:
            self.error_received.emit("PixelPilot Live is unavailable.")
            return False
        if not self.is_available:
            self.error_received.emit(self.unavailable_reason)
            return False

        rate_limit_error = self._active_rate_limit_error()
        if rate_limit_error is not None:
            self.error_received.emit(str(rate_limit_error))
            return False

        self._clear_manual_disconnect_request()
        logger.info("LIVE_SESSION_RECONNECT_REQUESTED")
        if self._transport is not None or self._connect_task is not None or self._reconnect_in_progress:
            return True

        submitted = self._submit_async(self._ensure_session_with_retry())
        if submitted:
            self.session_state_changed.emit("connecting")
            return True

        self.error_received.emit("Failed to reconnect PixelPilot Live.")
        return False

    def _record_user_steering(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        if not self._current_goal:
            self._current_goal = clean
        clear_stop = getattr(self.agent, "clear_stop_request", None)
        if callable(clear_stop):
            try:
                clear_stop()
            except Exception:
                pass
        self.agent.current_task = clean
        self._recent_user_steering.append(clean)
        return clean

    def _begin_text_turn(
        self,
        text: str,
        *,
        wait_for_result: bool,
    ) -> tuple[Optional[dict[str, Any]], str]:
        clean = str(text or "").strip()
        if not clean:
            return None, "Message is empty."
        if not self.enabled:
            return None, "PixelPilot Live is unavailable."
        self._clear_stale_text_turn_if_idle(reason="new_submit")
        with self._turn_state_lock:
            if self._active_text_turn_id is not None:
                return None, "Wait for the current reply before sending another command."
            self._next_text_turn_id += 1
            turn_id = self._next_text_turn_id
            waiter = {
                "turn_id": turn_id,
                "submitted_text": clean,
                "assistant_text": "",
                "error": "",
                "event": threading.Event() if wait_for_result else None,
            }
            self._turn_waiters[turn_id] = waiter
            self._active_text_turn_id = turn_id
            self._last_typed_turn_activity_at = time.monotonic()

        self._record_user_steering(clean)
        self._mark_live_activity("typed_turn")
        self.session_state_changed.emit("thinking")
        return waiter, ""

    def _finish_text_turn(
        self,
        *,
        assistant_text: str = "",
        error: str = "",
    ) -> None:
        _turn_id, waiter, timer = self._take_active_text_turn()
        if timer is not None:
            timer.cancel()
        if not waiter:
            return
        self._resolve_text_turn_waiter(
            waiter,
            assistant_text=assistant_text,
            error=error,
        )
        if str(self._pending_text_nudge or "").strip():
            self._schedule_pending_text_nudge_flush(
                delay_s=Config.LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS,
                reason="turn_finished",
            )
        if self._pending_text_commands:
            self._schedule_pending_text_command_flush(reason="turn_finished")

    @staticmethod
    def _resolve_text_turn_waiter(
        waiter: Optional[dict[str, Any]],
        *,
        assistant_text: str = "",
        error: str = "",
    ) -> None:
        if not waiter:
            return
        waiter["assistant_text"] = str(assistant_text or "").strip()
        waiter["error"] = str(error or "").strip()
        event = waiter.get("event")
        if event is not None:
            event.set()

    def _take_active_text_turn(
        self,
        *,
        expected_turn_id: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[dict[str, Any]], Optional[threading.Timer]]:
        with self._turn_state_lock:
            turn_id = self._active_text_turn_id
            if turn_id is None:
                return None, None, None
            if expected_turn_id is not None and turn_id != expected_turn_id:
                return None, None, None
            waiter = self._turn_waiters.pop(turn_id, None)
            timer = self._typed_turn_idle_finish_timer
            self._typed_turn_idle_finish_timer = None
            self._typed_turn_idle_finish_generation += 1
            self._active_text_turn_id = None
        return turn_id, waiter, timer

    def _cancel_typed_turn_idle_finish_timer(self) -> None:
        timer: Optional[threading.Timer] = None
        with self._turn_state_lock:
            if self._typed_turn_idle_finish_timer is None:
                return
            timer = self._typed_turn_idle_finish_timer
            self._typed_turn_idle_finish_timer = None
            self._typed_turn_idle_finish_generation += 1
        if timer is not None:
            timer.cancel()

    def _note_typed_turn_activity(self) -> None:
        timer: Optional[threading.Timer] = None
        with self._turn_state_lock:
            if self._active_text_turn_id is None:
                return
            self._last_typed_turn_activity_at = time.monotonic()
            if self._typed_turn_idle_finish_timer is not None:
                timer = self._typed_turn_idle_finish_timer
                self._typed_turn_idle_finish_timer = None
                self._typed_turn_idle_finish_generation += 1
        if timer is not None:
            timer.cancel()

    def _speaker_queue_is_idle(self) -> bool:
        queue = self._speaker_queue
        if queue is None:
            return True
        try:
            return queue.qsize() <= 0
        except Exception:
            return True

    def _has_active_text_turn(self) -> bool:
        with self._turn_state_lock:
            return self._active_text_turn_id is not None

    def _live_session_is_busy_for_idle(self) -> bool:
        return bool(
            self._connect_task is not None
            or self._reconnect_in_progress
            or self.broker.has_pending()
            or self._has_active_text_turn()
            or not self._speaker_queue_is_idle()
        )

    def _cancel_idle_disconnect_task(self) -> None:
        task = self._idle_disconnect_task
        self._idle_disconnect_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_disconnect_after_reply_task(self, *, clear_message: bool = True) -> None:
        task = self._disconnect_after_reply_task
        self._disconnect_after_reply_task = None
        current_task = None
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if task is not None and not task.done() and task is not current_task:
            task.cancel()
        if clear_message:
            self._pending_disconnect_status_message = ""

    def _queue_disconnect_after_assistant_turn(self, *, status_message: str = "") -> None:
        message = str(status_message or "").strip()
        if message:
            self._pending_disconnect_status_message = message
        elif not str(self._pending_disconnect_status_message or "").strip():
            self._pending_disconnect_status_message = "PixelPilot Live disconnected."

        if self._disconnect_after_reply_task is not None and not self._disconnect_after_reply_task.done():
            return
        self._disconnect_after_reply_task = asyncio.create_task(
            self._disconnect_after_reply_timeout_loop()
        )

    async def _wait_for_speaker_idle_before_disconnect(self) -> None:
        deadline = time.monotonic() + max(
            0.5,
            float(getattr(Config, "LIVE_DISCONNECT_AFTER_REPLY_TIMEOUT_SECONDS", 8.0) or 8.0),
        )
        while (
            self.enabled
            and self._transport is not None
            and not self._shutdown_event.is_set()
            and time.monotonic() < deadline
        ):
            audio_tail_remaining = self._audio_output_suppressed_until - time.monotonic()
            if self._speaker_queue_is_idle() and audio_tail_remaining <= 0.0:
                return
            await asyncio.sleep(0.05)

    async def _complete_pending_disconnect_after_reply(self) -> None:
        status_message = str(self._pending_disconnect_status_message or "").strip()
        if not status_message:
            return
        self._pending_disconnect_status_message = ""
        self._cancel_disconnect_after_reply_task(clear_message=False)
        await self._wait_for_speaker_idle_before_disconnect()
        await self._disconnect_after_tool_call(status_message=status_message)

    async def _disconnect_after_reply_timeout_loop(self) -> None:
        timeout_s = max(
            0.5,
            float(getattr(Config, "LIVE_DISCONNECT_AFTER_REPLY_TIMEOUT_SECONDS", 8.0) or 8.0),
        )
        try:
            await asyncio.sleep(timeout_s)
            if not str(self._pending_disconnect_status_message or "").strip():
                return
            fragments = self._drain_transcript_buffers(emit_final=True)
            if self._has_active_text_turn():
                assistant_text = str(fragments.get("assistant") or "").strip()
                self._finish_text_turn(
                    assistant_text=assistant_text,
                    error="" if assistant_text else "PixelPilot Live disconnected by request.",
                )
            await self._complete_pending_disconnect_after_reply()
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._disconnect_after_reply_task is current:
                self._disconnect_after_reply_task = None

    def _mark_live_activity(self, reason: str = "") -> None:
        self._last_live_activity_at = time.monotonic()
        self._schedule_idle_disconnect(reason=reason)

    def _schedule_idle_disconnect(self, *, reason: str = "") -> None:
        timeout_s = max(0.0, float(getattr(Config, "LIVE_SESSION_IDLE_DISCONNECT_SECONDS", 60.0) or 0.0))
        if timeout_s <= 0.0 or not self.enabled or self._transport is None or self._shutdown_event.is_set():
            self._cancel_idle_disconnect_task()
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cancel_idle_disconnect_task()
        self._idle_disconnect_task = asyncio.create_task(self._idle_disconnect_loop(reason=reason))

    async def _idle_disconnect_loop(self, *, reason: str = "") -> None:
        del reason
        timeout_s = max(0.0, float(getattr(Config, "LIVE_SESSION_IDLE_DISCONNECT_SECONDS", 60.0) or 0.0))
        try:
            while (
                timeout_s > 0.0
                and self.enabled
                and self._transport is not None
                and not self._shutdown_event.is_set()
            ):
                idle_for = time.monotonic() - self._last_live_activity_at
                remaining = timeout_s - idle_for
                if remaining > 0.0:
                    await asyncio.sleep(min(remaining, 5.0))
                    continue
                if self._live_session_is_busy_for_idle():
                    self._last_live_activity_at = time.monotonic()
                    await asyncio.sleep(min(timeout_s, 5.0))
                    continue

                logger.info("LIVE_IDLE_DISCONNECT idle_seconds=%.2f", idle_for)
                self.status_received.emit(
                    "PixelPilot Live went idle after 1 minute. Type, voice, or wake word to reconnect."
                )
                self._manual_disconnect_requested = True
                self._cancel_go_away_reconnect_task()
                self._cancel_one_shot_tasks()
                self._cancel_typed_turn_idle_finish_timer()
                self._clear_pending_text_commands()
                self._clear_pending_text_nudge()
                if self._voice_enabled:
                    self._voice_enabled = False
                    self.voice_active_changed.emit(False)
                await self._disconnect_session(close_client=True)
                return
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._idle_disconnect_task is current:
                self._idle_disconnect_task = None

    def _schedule_typed_turn_idle_finish(self, *, reason: str) -> None:
        delay_s = max(0.0, float(Config.LIVE_TYPED_TURN_IDLE_FINISH_SECONDS))
        timer_to_cancel: Optional[threading.Timer] = None
        next_timer: Optional[threading.Timer] = None
        turn_id: Optional[int] = None
        generation: Optional[int] = None
        with self._turn_state_lock:
            self._last_typed_turn_activity_at = time.monotonic()
            if self._typed_turn_idle_finish_timer is not None:
                timer_to_cancel = self._typed_turn_idle_finish_timer
                self._typed_turn_idle_finish_timer = None
                self._typed_turn_idle_finish_generation += 1
            turn_id = self._active_text_turn_id
            if (
                turn_id is None
                or self._shutdown_event.is_set()
                or self.broker.has_pending()
                or not self._speaker_queue_is_idle()
            ):
                generation = None
            else:
                self._typed_turn_idle_finish_generation += 1
                generation = self._typed_turn_idle_finish_generation
                next_timer = threading.Timer(
                    delay_s,
                    self._maybe_finish_stale_typed_turn,
                    kwargs={
                        "turn_id": turn_id,
                        "generation": generation,
                        "reason": reason,
                    },
                )
                next_timer.daemon = True
                self._typed_turn_idle_finish_timer = next_timer
        if timer_to_cancel is not None:
            timer_to_cancel.cancel()
        if next_timer is not None and turn_id is not None and generation is not None:
            logger.debug(
                "Scheduled typed-turn idle fallback for turn %s in %.2fs (%s).",
                turn_id,
                delay_s,
                reason,
            )
            next_timer.start()

    def _maybe_finish_stale_typed_turn(
        self,
        *,
        turn_id: int,
        generation: int,
        reason: str,
    ) -> None:
        waiter: Optional[dict[str, Any]] = None
        timer: Optional[threading.Timer] = None
        with self._turn_state_lock:
            if self._typed_turn_idle_finish_generation != generation:
                return
            if self._active_text_turn_id != turn_id:
                return
            if self._shutdown_event.is_set() or self.broker.has_pending():
                return
            idle_seconds = max(0.0, float(Config.LIVE_TYPED_TURN_IDLE_FINISH_SECONDS))
            if idle_seconds > 0.0 and (
                time.monotonic() - self._last_typed_turn_activity_at
            ) < idle_seconds:
                return
            if not self._speaker_queue_is_idle():
                return
        _resolved_turn_id, waiter, timer = self._take_active_text_turn(expected_turn_id=turn_id)
        if waiter is None:
            if timer is not None:
                timer.cancel()
            return
        if timer is not None:
            timer.cancel()
        fragments = self._drain_transcript_buffers(emit_final=True)
        self._resolve_text_turn_waiter(
            waiter,
            assistant_text=fragments.get("assistant", ""),
        )
        logger.debug(
            "Released stale typed live turn %s after idle timeout (%s).",
            turn_id,
            reason,
        )
        if self.enabled:
            self.session_state_changed.emit("listening")
        if str(self._pending_text_nudge or "").strip():
            self._schedule_pending_text_nudge_flush(
                delay_s=Config.LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS,
                reason="stale_turn_released",
            )
        if self._pending_text_commands:
            self._schedule_pending_text_command_flush(reason="stale_turn_released")

    def _clear_stale_text_turn_if_idle(self, *, reason: str) -> bool:
        turn_id: Optional[int] = None
        waiter: Optional[dict[str, Any]] = None
        timer: Optional[threading.Timer] = None
        with self._turn_state_lock:
            turn_id = self._active_text_turn_id
            if turn_id is None or self._shutdown_event.is_set():
                return False
            if self.broker.has_pending() or not self._speaker_queue_is_idle():
                return False
            idle_seconds = max(0.0, float(Config.LIVE_TYPED_TURN_IDLE_FINISH_SECONDS))
            if idle_seconds > 0.0 and (
                time.monotonic() - self._last_typed_turn_activity_at
            ) < idle_seconds:
                return False
        turn_id, waiter, timer = self._take_active_text_turn(expected_turn_id=turn_id)
        if waiter is None:
            if timer is not None:
                timer.cancel()
            return False
        if timer is not None:
            timer.cancel()
        fragments = self._drain_transcript_buffers(emit_final=True)
        self._resolve_text_turn_waiter(
            waiter,
            assistant_text=fragments.get("assistant", ""),
        )
        logger.debug("Cleared stale typed live turn %s before new input (%s).", turn_id, reason)
        if self.enabled:
            self.session_state_changed.emit("listening")
        if str(self._pending_text_nudge or "").strip():
            self._schedule_pending_text_nudge_flush(
                delay_s=Config.LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS,
                reason="stale_turn_cleared",
            )
        if self._pending_text_commands:
            self._schedule_pending_text_command_flush(reason="stale_turn_cleared")
        return True

    def _cancel_pending_text_nudge_timer(self) -> None:
        timer: Optional[threading.Timer] = None
        with self._turn_state_lock:
            if self._pending_text_nudge_timer is None:
                return
            timer = self._pending_text_nudge_timer
            self._pending_text_nudge_timer = None
            self._pending_text_nudge_generation += 1
        if timer is not None:
            timer.cancel()

    def _clear_pending_text_nudge(self) -> None:
        self._cancel_pending_text_nudge_timer()
        with self._turn_state_lock:
            self._pending_text_nudge = ""
            self._soft_interrupt_requested = False

    def _schedule_pending_text_nudge_flush(self, *, delay_s: float, reason: str) -> None:
        timer_to_cancel: Optional[threading.Timer] = None
        next_timer: Optional[threading.Timer] = None
        generation: Optional[int] = None
        with self._turn_state_lock:
            if (
                not str(self._pending_text_nudge or "").strip()
                or self._shutdown_event.is_set()
            ):
                return
            if self._pending_text_nudge_timer is not None:
                timer_to_cancel = self._pending_text_nudge_timer
                self._pending_text_nudge_timer = None
                self._pending_text_nudge_generation += 1
            self._pending_text_nudge_generation += 1
            generation = self._pending_text_nudge_generation
            next_timer = threading.Timer(
                max(0.0, float(delay_s)),
                self._submit_pending_text_nudge_if_ready,
                kwargs={
                    "generation": generation,
                    "reason": reason,
                },
            )
            next_timer.daemon = True
            self._pending_text_nudge_timer = next_timer
        if timer_to_cancel is not None:
            timer_to_cancel.cancel()
        if next_timer is not None:
            logger.debug(
                "Scheduled pending live steering update in %.2fs (%s).",
                max(0.0, float(delay_s)),
                reason,
            )
            next_timer.start()

    def _restore_pending_text_nudge(self, text: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        with self._turn_state_lock:
            if not str(self._pending_text_nudge or "").strip():
                self._pending_text_nudge = clean

    @staticmethod
    def _build_soft_interrupt_prompt() -> str:
        return (
            "User steering update received. Stop the current plan at the next safe boundary. "
            "Do not start another desktop action until you have incorporated the latest steering update."
        )

    @staticmethod
    def _build_text_nudge_prompt(text: str) -> str:
        clean = str(text or "").strip()
        return (
            "User steering update. This is the latest user instruction and overrides any earlier "
            "steering update that conflicts with it. Revise your current reply or plan now. "
            f"Latest user instruction: {json.dumps(clean, ensure_ascii=True)}"
        )

    def _request_soft_interrupt_for_pending_nudge(self) -> None:
        should_send_prompt = False
        with self._turn_state_lock:
            if not self._soft_interrupt_requested:
                self._soft_interrupt_requested = True
                should_send_prompt = True
        self.broker.cancel_current_action("User updated the task. Finish at a safe boundary.")
        if should_send_prompt and self.enabled and self._transport is not None:
            self.session_state_changed.emit("interrupted")
            submitted = self._submit_async(
                self._send_text(self._build_soft_interrupt_prompt()),
                ensure_loop=False,
            )
            if not submitted:
                logger.debug("Failed to send soft-interrupt steering prompt to Gemini Live.")

    def _submit_pending_text_nudge_if_ready(
        self,
        *,
        generation: Optional[int] = None,
        reason: str,
    ) -> bool:
        pending_text = ""
        active_turn = False
        timer_to_cancel: Optional[threading.Timer] = None
        with self._turn_state_lock:
            if generation is not None and self._pending_text_nudge_generation != generation:
                return False
            pending_text = str(self._pending_text_nudge or "").strip()
            if (
                not pending_text
                or self._shutdown_event.is_set()
                or self.broker.has_pending()
            ):
                return False
            active_turn = self._active_text_turn_id is not None
            timer_to_cancel = self._pending_text_nudge_timer
            self._pending_text_nudge_timer = None
            self._pending_text_nudge_generation += 1
            self._pending_text_nudge = ""
            self._soft_interrupt_requested = False
        if timer_to_cancel is not None:
            timer_to_cancel.cancel()

        if active_turn:
            submitted = self._submit_async(self._send_text(self._build_text_nudge_prompt(pending_text)))
            if submitted:
                self._note_typed_turn_activity()
                logger.debug("Delivered live steering update into the active turn (%s).", reason)
                return True
            self._restore_pending_text_nudge(pending_text)
            self.error_received.emit("Failed to send the latest steering update to PixelPilot Live.")
            return False

        waiter, error_message = self._begin_text_turn(pending_text, wait_for_result=False)
        if waiter is None:
            self._restore_pending_text_nudge(pending_text)
            if error_message:
                logger.debug(
                    "Deferred live steering update remains queued (%s): %s",
                    reason,
                    error_message,
                )
            return False

        submitted = self._submit_async(self._send_text(str(waiter["submitted_text"])))
        if submitted:
            logger.debug("Started a fresh live turn from the latest steering update (%s).", reason)
            return True

        self._finish_text_turn(error="Failed to send the latest steering update to PixelPilot Live.")
        self._restore_pending_text_nudge(pending_text)
        self.error_received.emit("Failed to send the latest steering update to PixelPilot Live.")
        return False

    def _clear_pending_text_commands(self) -> None:
        with self._turn_state_lock:
            self._pending_text_commands.clear()
            self._pending_text_command_flush_in_progress = False

    def _queue_pending_text_command(self, text: str) -> tuple[int, bool]:
        clean = str(text or "").strip()
        if not clean:
            return 0, False

        with self._turn_state_lock:
            existing = list(self._pending_text_commands)
            replaced = clean in existing
            retained = [item for item in existing if item != clean]
            queue_maxlen = self._pending_text_commands.maxlen
            self._pending_text_commands.clear()
            self._pending_text_commands.append(clean)
            if queue_maxlen is None:
                for item in retained:
                    self._pending_text_commands.append(item)
            else:
                for item in retained:
                    if len(self._pending_text_commands) >= queue_maxlen:
                        break
                    self._pending_text_commands.append(item)
            depth = len(self._pending_text_commands)
        return depth, replaced

    def _restore_pending_text_command(self, text: str) -> None:
        self._queue_pending_text_command(text)

    def _schedule_pending_text_command_flush(self, *, reason: str) -> bool:
        if self._active_rate_limit_error() is not None:
            return False

        with self._turn_state_lock:
            if (
                self._pending_text_command_flush_in_progress
                or not self._pending_text_commands
                or self._shutdown_event.is_set()
            ):
                return False
            self._pending_text_command_flush_in_progress = True

        submitted = self._submit_async(
            self._submit_pending_text_command_if_ready(reason=reason)
        )
        if submitted:
            return True

        with self._turn_state_lock:
            self._pending_text_command_flush_in_progress = False
        return False

    async def _submit_pending_text_command_if_ready(self, *, reason: str) -> bool:
        queued_text = ""
        try:
            with self._turn_state_lock:
                if (
                    not self._pending_text_commands
                    or self._shutdown_event.is_set()
                    or self._active_text_turn_id is not None
                    or self.broker.has_pending()
                ):
                    return False
                queued_text = str(self._pending_text_commands.popleft()).strip()

            waiter, error_message = self._begin_text_turn(queued_text, wait_for_result=False)
            if waiter is None:
                self._restore_pending_text_command(queued_text)
                if error_message:
                    logger.debug(
                        "Queued live text command remains pending (%s): %s",
                        reason,
                        error_message,
                    )
                return False

            try:
                await self._send_text(str(waiter["submitted_text"]))
            except Exception:
                self._finish_text_turn(error="Failed to send the queued command to PixelPilot Live.")
                self._restore_pending_text_command(queued_text)
                self.error_received.emit("Failed to send the queued command to PixelPilot Live.")
                return False

            logger.debug(
                "Started a queued live text command (%s): %s",
                reason,
                queued_text,
            )
            return True
        finally:
            reschedule = False
            with self._turn_state_lock:
                self._pending_text_command_flush_in_progress = False
                reschedule = bool(
                    self._pending_text_commands
                    and self._active_text_turn_id is None
                    and not self._shutdown_event.is_set()
                    and not self.broker.has_pending()
                    and self._active_rate_limit_error() is None
                )
            if reschedule:
                self._schedule_pending_text_command_flush(reason="queue_followup")

    def _handle_queued_text_submission(self, text: str) -> dict[str, Any]:
        clean = self._record_user_steering(text)
        if not clean:
            return {
                "ok": False,
                "status": "rejected",
                "message": "Message is empty.",
            }

        self._log_user_request(clean, source="queued_connecting")
        self._session_store_call("record_user_text", clean, source="queued_connecting")

        self._clear_manual_disconnect_request()
        depth, replaced = self._queue_pending_text_command(clean)
        self.session_state_changed.emit("connecting")
        self._schedule_pending_text_command_flush(reason="queued_submission")

        if depth > 1:
            message = (
                "Updated the queued instructions. The newest request will run first when PixelPilot Live is ready."
                if replaced
                else "Queued your message. The newest queued request will run first when PixelPilot Live is ready."
            )
        else:
            message = (
                "Updated the queued instruction. PixelPilot Live will send it as soon as the session is ready."
                if replaced
                else "PixelPilot Live is still connecting. I queued your message and will send it as soon as the session is ready."
            )

        return {
            "ok": True,
            "status": "queued_connecting",
            "message": message,
            "queue_depth": depth,
        }

    def _handle_text_nudge_submission(self, text: str) -> dict[str, Any]:
        clean = self._record_user_steering(text)
        if not clean:
            return {
                "ok": False,
                "status": "rejected",
                "message": "Message is empty.",
            }

        self._log_user_request(clean, source="steering_nudge")
        self._session_store_call("record_user_text", clean, source="steering_nudge")

        replaced = False
        with self._turn_state_lock:
            replaced = bool(str(self._pending_text_nudge or "").strip())
            self._pending_text_nudge = clean
        self._note_typed_turn_activity()

        if self.broker.has_pending():
            self._request_soft_interrupt_for_pending_nudge()
            return {
                "ok": True,
                "status": "nudge_queued",
                "message": (
                    "Updated the pending steering. I will switch at the next safe boundary."
                    if replaced
                    else "Steering update queued. I will switch at the next safe boundary."
                ),
            }

        self._schedule_pending_text_nudge_flush(
            delay_s=Config.LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS,
            reason="user_nudge",
        )
        return {
            "ok": True,
            "status": "nudge_sent",
            "message": (
                "Updated the steering. I am adapting now."
                if replaced
                else "Steering update accepted. I am adapting now."
            ),
        }

    def submit_text(self, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()
        if self.is_connection_pending:
            return self._handle_queued_text_submission(clean)

        waiter, error_message = self._begin_text_turn(text, wait_for_result=False)
        if waiter is None:
            if error_message == "Wait for the current reply before sending another command.":
                if self.is_connection_pending:
                    return self._handle_queued_text_submission(clean)
                return self._handle_text_nudge_submission(clean)
            if error_message:
                self.error_received.emit(error_message)
            return {
                "ok": False,
                "status": "rejected",
                "message": error_message or "Unable to submit command.",
            }
        self._log_user_request(clean, source="typed_submit")
        self._session_store_call("record_user_text", clean, source="typed_submit")
        self._clear_manual_disconnect_request()
        queued_for_connect = self.is_connection_pending
        submitted = self._submit_async(self._send_text(str(waiter["submitted_text"])))
        if not submitted:
            self._finish_text_turn(error="Failed to send the command to PixelPilot Live.")
            self.error_received.emit("Failed to send the command to PixelPilot Live.")
            return {
                "ok": False,
                "status": "submit_failed",
                "message": "Failed to send the command to PixelPilot Live.",
            }
        if queued_for_connect:
            self.session_state_changed.emit("connecting")
            return {
                "ok": True,
                "status": "queued_connecting",
                "message": (
                    "PixelPilot Live is still connecting. I queued your message and will send it as soon as the session is ready."
                ),
            }
        return {
            "ok": True,
            "status": "submitted",
            "message": "",
        }

    def submit_text_and_wait(self, text: str, timeout_s: Optional[float] = None) -> dict[str, Any]:
        waiter, error_message = self._begin_text_turn(text, wait_for_result=True)
        if waiter is None:
            return {
                "ok": False,
                "error": "turn_rejected",
                "message": error_message or "Unable to submit command.",
                "text": "",
            }

        self._log_user_request(str(waiter.get("submitted_text") or text), source="typed_wait")
        self._session_store_call(
            "record_user_text",
            str(waiter.get("submitted_text") or text),
            source="typed_wait",
        )
        self._clear_manual_disconnect_request()
        submitted = self._submit_async(self._send_text(str(waiter["submitted_text"])))
        if not submitted:
            self._finish_text_turn(error="Failed to send the command to PixelPilot Live.")
            return {
                "ok": False,
                "error": "submit_failed",
                "message": "Failed to send the command to PixelPilot Live.",
                "text": "",
            }

        timeout_value = max(1.0, float(timeout_s or 90.0))
        event = waiter.get("event")
        if event is None or not event.wait(timeout=timeout_value):
            self._finish_text_turn(error="Timed out waiting for PixelPilot Live to finish the turn.")
            return {
                "ok": False,
                "error": "timeout",
                "message": "Timed out waiting for PixelPilot Live to finish the turn.",
                "text": "",
            }

        assistant_text = str(waiter.get("assistant_text") or "").strip()
        error_text = str(waiter.get("error") or "").strip()
        if error_text:
            return {
                "ok": False,
                "error": "turn_failed",
                "message": error_text,
                "text": assistant_text,
            }
        return {
            "ok": True,
            "error": "",
            "message": "Turn completed.",
            "text": assistant_text,
        }

    def start_voice(self, mode: str = "continuous") -> bool:
        if not self.enabled:
            self.error_received.emit("PixelPilot Live is unavailable.")
            return False
        if not self._voice_supported:
            self.error_received.emit(
                f"Voice is unavailable for {self._provider_config.display_name} with the current PixelPilot audio transport. Please type your instruction."
            )
            return False
        clear_stop = getattr(self.agent, "clear_stop_request", None)
        if callable(clear_stop):
            try:
                clear_stop()
            except Exception:
                pass
        self._clear_manual_disconnect_request()
        self._configure_voice_mode(mode)
        self._mark_live_activity("voice_start")
        if self._voice_enabled:
            return True
        submitted = self._submit_async(self._start_voice_async())
        if not submitted:
            self._cancel_one_shot_tasks()
            message = str(getattr(self, "unavailable_reason", "") or "").strip()
            if not message:
                message = (
                    "PixelPilot Live voice could not start because the background session was unavailable."
                )
            self.error_received.emit(message)
            return False
        self._voice_enabled = True
        self.voice_active_changed.emit(True)
        self.session_state_changed.emit("connecting")
        return True

    def stop_voice(self) -> bool:
        was_active = bool(self._voice_enabled or (self._mic_task and not self._mic_task.done()))
        self._voice_enabled = False
        self._mark_live_activity("voice_stop")
        self._cancel_typed_turn_idle_finish_timer()
        self._cancel_pending_text_nudge_timer()
        self._cancel_one_shot_tasks()
        submitted = self._submit_async(
            self._stop_voice_async(emit_voice_inactive=was_active),
            ensure_loop=False,
        )
        if not submitted and was_active:
            self.voice_active_changed.emit(False)
            if self.enabled:
                self.session_state_changed.emit("listening")
        return submitted

    def request_stop(self) -> None:
        self.broker.cancel_current_action("Stop requested. Finish at a safe boundary.")
        if self.enabled and self._transport is not None:
            self.session_state_changed.emit("interrupted")
            self._submit_async(
                self._send_text(
                    "Stop the current plan at the next safe boundary and wait for new instructions."
                ),
                ensure_loop=False,
            )

    def notify_workspace_changed(self, workspace: str) -> None:
        self._workspace = (workspace or "user").strip().lower() or "user"

    def _clear_session_context(self, *, reason: str = "") -> None:
        try:
            self.broker.cancel_current_action(reason or "Starting a fresh live session.")
        except Exception:
            pass
        self._resume_handle = None
        self._resume_pending_user_buffer = ""
        self._resume_pending_assistant_buffer = ""
        self._user_buffer = ""
        self._assistant_buffer = ""
        self._current_goal = ""
        self._recent_user_steering.clear()
        self._recent_action_updates.clear()
        self._pending_capture_paths.clear()
        self._cancel_disconnect_after_reply_task()
        self._clear_pending_text_commands()
        self._clear_speaker_queue()
        self._cancel_typed_turn_idle_finish_timer()
        self._clear_pending_text_nudge()
        self._soft_interrupt_requested = False
        self._finish_text_turn(error=reason)

    async def _restart_session_fresh(self) -> None:
        if self._shutdown_event.is_set():
            return
        if self._reconnect_in_progress:
            return
        self._reconnect_in_progress = True
        try:
            await self._disconnect_session(reconnecting=True)
            if self._should_auto_reconnect():
                await self._ensure_session()
        finally:
            self._reconnect_in_progress = False

    def notify_mode_changed(self, mode: object) -> None:
        self._mode = mode
        self._session_store_call(
            "record_session_event",
            "mode_changed",
            {
                "mode": self._mode_key(mode),
            },
        )
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self._clear_manual_disconnect_request()
        self._clear_session_context(reason="Mode changed. Started a fresh live session.")
        self._reset_reasoning_escalation_state()
        if self.enabled:
            self._submit_async(self._restart_session_fresh(), ensure_loop=False)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._cancel_typed_turn_idle_finish_timer()
        self._cancel_idle_disconnect_task()
        self._cancel_disconnect_after_reply_task()
        self._clear_pending_text_commands()
        self._clear_pending_text_nudge()
        self._cancel_one_shot_tasks()
        self._finish_text_turn(error="PixelPilot Live session shut down.")
        self._reset_reasoning_escalation_state()
        self._manual_disconnect_requested = False
        self._voice_enabled = False
        self._voice_mode = "continuous"
        self._one_shot_engaged = False
        self.voice_active_changed.emit(False)
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._disconnect_session(close_client=True),
                self._loop,
            )
            try:
                future.result(timeout=3.0)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=2.0)
        self.broker.shutdown()

    def _ensure_loop(self) -> bool:
        if not self.is_available:
            return False
        if self._loop and self._loop.is_running():
            return True

        loop_created = threading.Event()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop_created.set()
            loop.run_forever()

            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

        self._loop_thread = threading.Thread(target=_runner, name="GeminiLiveSession", daemon=True)
        self._loop_thread.start()
        loop_created.wait(timeout=2.0)
        return self._loop is not None

    def _submit_async(
        self,
        coro: Optional[Coroutine[Any, Any, Any]],
        *,
        ensure_loop: bool = True,
    ) -> bool:
        if coro is None:
            return False
        if ensure_loop:
            if not self._ensure_loop():
                try:
                    coro.close()
                except Exception:
                    pass
                return False
        elif not (self._loop and self._loop.is_running()):
            try:
                coro.close()
            except Exception:
                pass
            return False
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(self._handle_background_future)
        return True

    def _handle_background_future(self, future) -> None:
        try:
            future.result()
        except asyncio.CancelledError:
            return
        except RateLimitError as exc:
            self._apply_rate_limit_cooldown(exc, source="background_task")
            logger.warning("Gemini Live rate limited: %s", exc)
            self.session_state_changed.emit("disconnected")
            self.error_received.emit(str(exc))
        except RuntimeError as exc:
            logger.warning("Gemini Live error: %s", exc)
            self.session_state_changed.emit("disconnected")
            self._finish_text_turn(error=str(exc))
            self.error_received.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Live background task failed")
            self._finish_text_turn(error=f"PixelPilot Live background task failed: {exc}")
            self.error_received.emit(f"PixelPilot Live background task failed: {exc}")

    async def _send_text(self, text: str) -> None:
        await self._send_realtime_text(str(text or ""))

    async def _send_realtime_text(self, text: str, *, allow_retry: bool = True) -> None:
        payload = str(text or "").strip()
        if not payload:
            return

        max_retries = 6 if allow_retry else 0
        attempt = 0
        while True:
            transport = await self._ensure_session_with_retry()
            try:
                await asyncio.wait_for(
                    transport.send_text(payload),
                    timeout=Config.LIVE_TEXT_SEND_TIMEOUT_SECONDS,
                )
                return
            except RateLimitError:
                raise
            except asyncio.TimeoutError as exc:
                if (
                    attempt < max_retries
                    and self._should_auto_reconnect()
                ):
                    logger.warning("Timed out sending realtime text; reconnecting live session.")
                    await self._reconnect_with_resume()
                    attempt += 1
                    continue
                raise RuntimeError("Timed out sending text to PixelPilot Live session.") from exc
            except Exception as exc:  # noqa: BLE001
                if (
                    attempt < max_retries
                    and self._should_auto_reconnect()
                    and self._is_recoverable_connection_error(exc)
                ):
                    logger.warning("Realtime text send failed; reconnecting live session.", exc_info=True)
                    await self._reconnect_with_resume()
                    attempt += 1
                    continue
                raise

    async def _start_voice_async(self) -> None:
        try:
            await self._ensure_session_with_retry()
            if self._mic_task and not self._mic_task.done():
                return
            if self._voice_mode == "one_shot":
                self._schedule_one_shot_timeout()
            self._mic_task = asyncio.create_task(self._microphone_loop())
            self.session_state_changed.emit("listening")
        except Exception:
            if self._voice_enabled:
                self._voice_enabled = False
                self._cancel_one_shot_tasks()
                self.voice_active_changed.emit(False)
            raise

    async def _ensure_session_with_retry(self, retries: int = 1):
        active_rate_limit_error = self._active_rate_limit_error()
        if active_rate_limit_error is not None:
            raise active_rate_limit_error

        attempt = 0
        retried_without_resumption = False
        while True:
            try:
                return await self._ensure_session()
            except RateLimitError as exc:
                self._apply_rate_limit_cooldown(exc, source="connect")
                raise
            except Exception as exc:  # noqa: BLE001
                if self._is_nonrecoverable_request_error(exc):
                    had_resume_handle = bool(str(self._resume_handle or "").strip())
                    if had_resume_handle and not retried_without_resumption:
                        retried_without_resumption = True
                        self._clear_resume_handle(reason=f"connect_rejected: {exc}")
                        logger.warning(
                            "Live session connect rejected resumption handle; retrying once without resumption."
                        )
                        await self._disconnect_session(reconnecting=True)
                        await asyncio.sleep(0.05)
                        continue

                    self._manual_disconnect_requested = True
                    self._cancel_go_away_reconnect_task()
                    await self._disconnect_session(close_client=True)
                    raise RuntimeError(
                        "PixelPilot Live rejected the connection request (invalid argument). "
                        "Tap to reconnect."
                    ) from exc

                if attempt >= retries or not self._is_recoverable_connection_error(exc):
                    raise
                delay_s = Config.LIVE_CONNECT_RETRY_BASE_DELAY_SECONDS * (attempt + 1)
                logger.warning(
                    "Live session connect failed (%s); retrying in %.2fs",
                    exc,
                    delay_s,
                )
                await self._disconnect_session(reconnecting=True)
                await asyncio.sleep(delay_s)
                attempt += 1

    async def _stop_voice_async(self, *, emit_voice_inactive: bool = True) -> None:
        if self._mic_task and not self._mic_task.done():
            # Ask the loop to exit cleanly first so PortAudio reads are not interrupted mid-call.
            self._voice_enabled = False
            try:
                await asyncio.wait_for(asyncio.shield(self._mic_task), timeout=0.8)
            except asyncio.TimeoutError:
                self._mic_task.cancel()
                await asyncio.gather(self._mic_task, return_exceptions=True)
        self._mic_task = None
        if self._transport is not None:
            try:
                await self._transport.send_audio_stream_end()
            except Exception as exc:  # noqa: BLE001
                if self._is_recoverable_connection_error(exc):
                    logger.warning(
                        "Live audio stream end failed; reconnecting session: %s",
                        exc,
                    )
                    if self._should_auto_reconnect():
                        await self._reconnect_with_resume()
                else:
                    logger.debug("Failed to flush live audio stream end", exc_info=True)
        self.audio_level_changed.emit(0.0)
        self._cancel_one_shot_tasks(keep_current=True)
        self._voice_mode = "continuous"
        self._one_shot_engaged = False
        if emit_voice_inactive:
            self.voice_active_changed.emit(False)
        if self.enabled:
            self.session_state_changed.emit("listening")

    async def _one_shot_timeout_loop(self) -> None:
        try:
            await asyncio.sleep(Config.WAKE_WORD_NO_SPEECH_TIMEOUT_SECONDS)
            if (
                not self._voice_enabled
                or self._voice_mode != "one_shot"
                or self._one_shot_engaged
            ):
                return
            phrase = str(Config.WAKE_WORD_PHRASE or "Hey Pixie").strip() or "Hey Pixie"
            self.status_received.emit(f'Wake word timed out. Say "{phrase}" and try again.')
            self._voice_enabled = False
            await self._stop_voice_async()
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._one_shot_timeout_task is current:
                self._one_shot_timeout_task = None

    async def _one_shot_finalize_loop(self) -> None:
        try:
            while self._voice_enabled and self._voice_mode == "one_shot":
                if self.broker.has_pending() or not self._speaker_queue_is_idle():
                    await asyncio.sleep(0.05)
                    continue
                remaining = self._audio_output_suppressed_until - time.monotonic()
                if remaining > 0.0:
                    await asyncio.sleep(min(remaining, 0.05))
                    continue
                extra_delay = max(0.0, float(Config.WAKE_WORD_RESUME_DELAY_SECONDS))
                if extra_delay > 0.0:
                    await asyncio.sleep(extra_delay)
                if not self._voice_enabled or self._voice_mode != "one_shot":
                    return
                self._voice_enabled = False
                await self._stop_voice_async()
                return
        except asyncio.CancelledError:
            return
        finally:
            current = asyncio.current_task()
            if self._one_shot_finalize_task is current:
                self._one_shot_finalize_task = None

    async def _ensure_session(self):
        if self._transport is not None:
            return self._transport

        if self._connect_task is not None:
            await asyncio.shield(self._connect_task)
            if self._transport is None:
                raise RuntimeError("PixelPilot Live session failed to start.")
            return self._transport

        self._connect_task = asyncio.create_task(self._connect_session())
        try:
            await asyncio.shield(self._connect_task)
        finally:
            self._connect_task = None
        if self._transport is None:
            raise RuntimeError("PixelPilot Live session failed to start.")
        return self._transport

    async def _connect_session(self):
        self.session_state_changed.emit("connecting")
        self._session_store_call(
            "record_session_event",
            "connecting",
            {
                "mode": self._mode_key(),
                "workspace": self._workspace,
            },
        )
        self._connect_in_progress = True
        transport = self._create_transport()
        try:
            config = self._build_connect_config()
            await transport.connect(model=self._provider_config.model, config=config)
            self._transport = transport
            self._session_started_at = time.monotonic()
            self._mark_live_activity("session_connected")
            queue_maxsize = (
                Config.LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS
                if Config.LIVE_AUDIO_LOSSLESS_MODE
                else Config.LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS
            )
            self._speaker_queue = asyncio.Queue(maxsize=queue_maxsize)
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._video_task = (
                asyncio.create_task(self._video_loop()) if self._video_stream_enabled else None
            )
            self._speaker_task = asyncio.create_task(self._speaker_loop())
            self._rotation_task = asyncio.create_task(self._rotation_loop())
            if bool(getattr(Config, "LIVE_USE_INTERNAL_UAC_DETECTOR", False)):
                self._uac_watchdog_task = asyncio.create_task(self._uac_watchdog_loop())
            else:
                self._uac_watchdog_task = None
            if self._is_guidance_mode():
                self._last_guidance_snapshot_signature = ""
                self._last_guidance_probe_sent_at = 0.0
                self._guidance_observer_task = asyncio.create_task(self._guidance_observer_loop())
            if self._voice_enabled and (self._mic_task is None or self._mic_task.done()):
                self._mic_task = asyncio.create_task(self._microphone_loop())
            self.session_state_changed.emit("listening")
            self._session_store_call(
                "record_session_event",
                "connected",
                {
                    "mode": self._mode_key(),
                    "workspace": self._workspace,
                    "voiceEnabled": self._voice_enabled,
                },
            )
            self._record_resume_metadata()
            if self._pending_text_commands:
                self._schedule_pending_text_command_flush(reason="session_connected")
            return transport
        except Exception:
            try:
                await transport.close(close_client=True)
            except Exception:
                logger.debug("Failed to close half-open live transport", exc_info=True)
            raise
        finally:
            self._connect_in_progress = False

    async def _disconnect_session(
        self,
        *,
        close_client: bool = False,
        reconnecting: bool = False,
    ) -> None:
        self._record_resume_metadata()
        current_task = asyncio.current_task()
        tasks = [
            self._mic_task,
            self._video_task,
            self._speaker_task,
            self._receive_task,
            self._rotation_task,
            self._guidance_observer_task,
            self._uac_watchdog_task,
            self._go_away_reconnect_task,
            self._idle_disconnect_task,
            self._disconnect_after_reply_task,
            self._connect_task,
        ]
        pending: list[asyncio.Task] = []
        for task in tasks:
            if task is None or task.done() or task is current_task:
                continue
            task.cancel()
            pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        self._mic_task = None
        self._video_task = None
        self._speaker_task = None
        self._receive_task = None
        self._rotation_task = None
        self._guidance_observer_task = None
        self._uac_watchdog_task = None
        self._go_away_reconnect_task = None
        self._idle_disconnect_task = None
        self._disconnect_after_reply_task = None
        self._connect_task = None
        self._connect_in_progress = False
        self._speaker_queue = None
        self.audio_level_changed.emit(0.0)
        self.assistant_audio_level_changed.emit(0.0)

        if self._transport is not None:
            try:
                await self._transport.close(close_client=close_client)
            except Exception:
                logger.debug("Failed to close live transport", exc_info=True)
        self._transport = None
        if reconnecting and self.enabled and not self._shutdown_event.is_set():
            self.session_state_changed.emit("connecting")
            self._session_store_call(
                "record_session_event",
                "reconnecting",
                {
                    "workspace": self._workspace,
                    "closeClient": bool(close_client),
                },
            )
        else:
            self.session_state_changed.emit("disconnected")
            self._session_store_call(
                "record_session_event",
                "disconnected",
                {
                    "workspace": self._workspace,
                    "closeClient": bool(close_client),
                },
            )

    def _build_connect_config(self) -> dict[str, Any]:
        guidance_mode = self._is_guidance_mode()
        system_instruction = (
            LIVE_GUIDANCE_SYSTEM_INSTRUCTION if guidance_mode else LIVE_SYSTEM_INSTRUCTION
        )
        mode_suffix = self._mode_instruction_suffix()
        if mode_suffix:
            system_instruction = f"{system_instruction}\n\n{mode_suffix}"
        resume_summary = self._build_resume_summary()
        if resume_summary:
            system_instruction = (
                f"{system_instruction}\n\n"
                f"{LIVE_SYSTEM_CONTEXT_PREFIX}\n"
                f"{resume_summary}"
            )

        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_instruction,
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": Config.LIVE_VOICE_NAME,
                    }
                }
            },
            "tools": [
                {
                    "function_declarations": self.tools.get_declarations(
                        read_only_only=guidance_mode
                    )
                }
            ],
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        if Config.LIVE_ENABLE_CONTEXT_WINDOW_COMPRESSION:
            config["context_window_compression"] = {"sliding_window": {}}
        effective_thinking_level = self._effective_thinking_level_for_config()
        if effective_thinking_level or Config.LIVE_INCLUDE_THOUGHTS:
            thinking_config: dict[str, Any] = {}
            if effective_thinking_level:
                thinking_config["thinking_level"] = effective_thinking_level
            if Config.LIVE_INCLUDE_THOUGHTS:
                thinking_config["include_thoughts"] = True
            config["thinking_config"] = thinking_config
        if types is not None and hasattr(types, "MediaResolution"):
            media_resolution = getattr(types.MediaResolution, "MEDIA_RESOLUTION_LOW", None)
            config["media_resolution"] = (
                media_resolution if Config.USE_DIRECT_API and media_resolution is not None else "MEDIA_RESOLUTION_LOW"
            )
        else:
            config["media_resolution"] = "MEDIA_RESOLUTION_LOW"

        # Keep direct SDK config typed (legacy stable behavior), but keep plain dicts for backend JSON transport.
        if Config.USE_DIRECT_API and types is not None and hasattr(types, "SessionResumptionConfig"):
            try:
                if self._resume_handle:
                    config["session_resumption"] = types.SessionResumptionConfig(
                        handle=self._resume_handle
                    )
                else:
                    config["session_resumption"] = types.SessionResumptionConfig()
            except TypeError:
                config["session_resumption"] = (
                    {"handle": self._resume_handle} if self._resume_handle else {}
                )
        else:
            config["session_resumption"] = (
                {"handle": self._resume_handle} if self._resume_handle else {}
            )
        return config

    async def _receive_loop(self) -> None:
        transport = await self._ensure_session_with_retry()
        try:
            while not self._shutdown_event.is_set():
                received_messages = False
                async for response in transport.events():
                    received_messages = True
                    if self._shutdown_event.is_set():
                        break
                    self._note_typed_turn_activity()

                    update = response.get("session_resumption_update")
                    if isinstance(update, dict):
                        handle = str(update.get("handle") or "").strip()
                        resumable = update.get("resumable")
                        if handle and resumable is not False:
                            self._resume_handle = handle
                        elif handle:
                            if self._resume_handle:
                                logger.debug(
                                    "Ignoring non-resumable Gemini Live session handle; future reconnects will start fresh."
                                )
                            self._resume_handle = None

                    tool_call = response.get("tool_call")
                    if tool_call:
                        self._mark_live_activity("tool_call")
                        self.session_state_changed.emit("acting")
                        await self._handle_tool_call(tool_call)

                    go_away = response.get("go_away")
                    if isinstance(go_away, dict):
                        logger.info(
                            "Gemini Live signaled connection expiry soon%s.",
                            f" (time_left={go_away.get('time_left')})"
                            if go_away.get("time_left")
                            else "",
                        )
                        self._schedule_go_away_reconnect(go_away)

                    server_content = response.get("server_content")
                    if not isinstance(server_content, dict):
                        continue

                    input_transcription = server_content.get("input_transcription")
                    if isinstance(input_transcription, dict):
                        text = str(input_transcription.get("text") or "")
                        if text:
                            self._mark_live_activity("user_transcript")
                            self._mark_one_shot_engaged()
                            self._user_buffer = self._merge_transcript_text(self._user_buffer, text)
                            self.transcript_received.emit("user", self._user_buffer, False)

                    output_text = ""
                    output_transcription = server_content.get("output_transcription")
                    if isinstance(output_transcription, dict):
                        output_text = str(output_transcription.get("text") or "")
                        output_text = self._guard_assistant_output_for_uac(output_text)
                        if output_text:
                            self._mark_live_activity("assistant_transcript")
                            self._assistant_buffer = self._merge_transcript_text(self._assistant_buffer, output_text)
                            self.transcript_received.emit("assistant", self._assistant_buffer, False)

                    model_turn = server_content.get("model_turn")
                    has_output_transcription = bool(output_text)
                    pending_audio_chunks: list[tuple[bytes, int]] = []
                    if isinstance(model_turn, dict):
                        for part in model_turn.get("parts") or []:
                            if not isinstance(part, dict):
                                continue
                            part_text = str(part.get("text") or "")
                            if part_text and bool(part.get("thought")):
                                logger.info(
                                    "LIVE_REASONING text=%s",
                                    self._truncate_log_text(part_text),
                                )
                            if (
                                part_text
                                and not has_output_transcription
                                and not bool(part.get("thought"))
                            ):
                                self._mark_live_activity("assistant_text")
                                part_text = self._guard_assistant_output_for_uac(part_text)
                                self._assistant_buffer = self._merge_transcript_text(
                                    self._assistant_buffer,
                                    part_text,
                                )
                                self.transcript_received.emit("assistant", self._assistant_buffer, False)

                            inline_data = part.get("inline_data")
                            data = inline_data.get("data") if isinstance(inline_data, dict) else None
                            mime_type = (
                                str(inline_data.get("mime_type") or "").lower()
                                if isinstance(inline_data, dict)
                                else ""
                            )
                            if data and self._speaker_queue is not None and (
                                not mime_type or mime_type.startswith("audio/")
                            ):
                                sample_rate = self._extract_audio_rate(mime_type)
                                pending_audio_chunks.append((bytes(data), sample_rate))

                    if bool(server_content.get("interrupted")):
                        self.session_state_changed.emit("interrupted")
                        self._finish_text_turn(error="PixelPilot Live interrupted the current turn.")
                        self._drain_transcript_buffers(emit_final=True)
                        self.assistant_audio_level_changed.emit(0.0)
                        self._clear_speaker_queue()
                        pending_audio_chunks.clear()

                    turn_completed = bool(server_content.get("turn_complete"))
                    generation_completed = bool(server_content.get("generation_complete"))
                    should_finalize_turn = bool(turn_completed)
                    if (
                        not should_finalize_turn
                        and generation_completed
                        and not pending_audio_chunks
                        and not self.broker.has_pending()
                    ):
                        should_finalize_turn = True

                    if generation_completed and not turn_completed:
                        self._schedule_typed_turn_idle_finish(reason="generation_complete")

                    if should_finalize_turn:
                        assistant_text = self._guard_assistant_output_for_uac(
                            str(self._assistant_buffer or "").strip()
                        )
                        self._assistant_buffer = assistant_text
                        self._drain_transcript_buffers(emit_final=True)
                        self._finish_text_turn(assistant_text=assistant_text)
                        self.assistant_audio_level_changed.emit(0.0)
                        self.session_state_changed.emit("listening")

                    for payload, sample_rate in pending_audio_chunks:
                        self._mark_live_activity("assistant_audio")
                        self._audio_output_suppressed_until = time.monotonic() + 0.25
                        self.assistant_audio_level_changed.emit(
                            self._compute_audio_level(payload)
                        )
                        await self._enqueue_speaker_audio(payload, sample_rate)

                    if turn_completed or generation_completed:
                        self._schedule_one_shot_finalize()
                    if should_finalize_turn and str(self._pending_disconnect_status_message or "").strip():
                        await self._complete_pending_disconnect_after_reply()
                        return

                if self._shutdown_event.is_set():
                    break

                if self._should_auto_reconnect():
                    if received_messages:
                        logger.debug(
                            "Live receive stream ended after events; re-subscribing to the existing session stream."
                        )
                        await asyncio.sleep(0.05)
                        continue

                    logger.warning(
                        "Live receive stream ended before any messages; reconnecting with session resumption."
                    )
                    await self._reconnect_with_resume()
                    return
                break
        except asyncio.CancelledError:
            raise
        except RateLimitError as exc:
            logger.warning("Live receive loop rate limited: %s", exc)
            self._apply_rate_limit_cooldown(exc, source="receive_loop")
            self._finish_text_turn(error=str(exc))
            self.error_received.emit(str(exc))
            if self.enabled and not self._shutdown_event.is_set():
                await self._disconnect_session()
            return
        except Exception as exc:  # noqa: BLE001
            if self._maybe_disable_image_input_for_error(exc):
                if self._should_auto_reconnect():
                    await self._reconnect_with_resume()
                return
            if self._is_recoverable_connection_error(exc):
                await self._recover_from_connection_error(exc, context="receive")
                return
            if self._is_nonrecoverable_request_error(exc):
                if self._should_auto_reconnect() and await self._recover_from_invalid_request_error(exc):
                    return
                logger.error(
                    "Live receive loop hit an invalid request and fresh recovery failed; disconnecting: %s",
                    exc,
                )
                message = "PixelPilot Live refreshed after an invalid request but could not reconnect. Tap to reconnect."
                self._finish_text_turn(error=message)
                self.error_received.emit(message)
                if self.enabled and not self._shutdown_event.is_set():
                    await self._disconnect_session(close_client=True)
                return
            logger.exception("Live receive loop failed")
            self._finish_text_turn(error=f"Live session error: {exc}")
            self.error_received.emit(f"Live session error: {exc}")
            if self.enabled and not self._shutdown_event.is_set():
                await self._disconnect_session(close_client=True)
            return

    async def _handle_tool_call(self, tool_call: Any) -> None:
        responses = []
        function_calls = []
        pending_reasoning_escalation = ""
        pending_disconnect_message = ""
        if isinstance(tool_call, dict):
            function_calls = tool_call.get("function_calls") or []
        for function_call in function_calls:
            if not isinstance(function_call, dict):
                continue
            call_id = str(function_call.get("id") or "")
            call_name = str(function_call.get("name") or "").strip()
            args = self._parse_args(function_call.get("args"))
            if not args:
                args = self._parse_args(function_call.get("arguments"))
            logger.info(
                "LIVE_TOOL_CALL_REQUEST id=%s name=%s args=%s",
                call_id,
                call_name,
                self._serialize_log_value(args),
            )
            self._session_store_call(
                "record_tool_call",
                call_name,
                args,
                call_id=call_id,
            )
            result = await asyncio.to_thread(
                self.tools.execute,
                call_name,
                args,
            )
            logger.info(
                "LIVE_TOOL_CALL_RESULT id=%s name=%s result=%s",
                call_id,
                call_name,
                self._serialize_log_value(result),
            )
            self._session_store_call(
                "record_tool_result",
                call_name,
                result if isinstance(result, dict) else {"value": result},
                call_id=call_id,
            )
            if call_name == "request_reasoning_escalation":
                escalation_result = result.get("result") if isinstance(result, dict) else None
                target_level = ""
                if isinstance(escalation_result, dict) and bool(
                    escalation_result.get("reconnect_required")
                ):
                    target_level = self._normalize_thinking_level(
                        escalation_result.get("effective_level")
                        or escalation_result.get("requested_level")
                    )
                if (
                    target_level
                    and self._thinking_level_rank(target_level)
                    > self._thinking_level_rank(pending_reasoning_escalation)
                ):
                    pending_reasoning_escalation = target_level
            if call_name == "disconnect_live_session":
                disconnect_result = result.get("result") if isinstance(result, dict) else None
                if isinstance(disconnect_result, dict) and bool(
                    disconnect_result.get("disconnect_requested")
                ):
                    pending_disconnect_message = str(
                        disconnect_result.get("status_message")
                        or result.get("message")
                        or ""
                    ).strip()
            responses.append(
                {
                    "id": function_call.get("id"),
                    "name": call_name,
                    "response": {"result": result},
                }
            )

        if responses and self._transport is not None:
            await self._transport.send_tool_responses(responses)
        if responses:
            self._record_resume_metadata()

        while self._pending_capture_paths and self._transport is not None:
            path, summary = self._pending_capture_paths.popleft()
            await self._send_capture_context(path, summary)

        if pending_disconnect_message:
            logger.info(
                "LIVE_TOOL_CALL_DISCONNECT status_message=%s",
                self._truncate_log_text(pending_disconnect_message),
            )
            self._queue_disconnect_after_assistant_turn(status_message=pending_disconnect_message)
            return

        if (
            pending_reasoning_escalation
            and self.enabled
            and not self._shutdown_event.is_set()
        ):
            logger.info(
                "LIVE_REASONING_ESCALATION reconnecting target_level=%s",
                pending_reasoning_escalation,
            )
            self.status_received.emit("Increasing reasoning depth and resuming...")
            await self._reconnect_with_resume()

    async def _disconnect_after_tool_call(self, *, status_message: str = "") -> None:
        self._cancel_go_away_reconnect_task()
        self._cancel_one_shot_tasks()
        self._clear_session_context(reason="PixelPilot Live disconnected by request.")
        self._reset_reasoning_escalation_state()
        self._manual_disconnect_requested = True
        self._voice_enabled = False
        self._voice_mode = "continuous"
        self._one_shot_engaged = False
        await self._disconnect_session(close_client=True)
        self.voice_active_changed.emit(False)
        if str(status_message or "").strip():
            self.status_received.emit(str(status_message))

    async def _uac_watchdog_loop(self) -> None:
        poll_interval_s = max(
            0.1,
            float(
                getattr(
                    Config,
                    "UAC_WATCHDOG_POLL_SECONDS",
                    getattr(Config, "UAC_IPC_POLL_INTERVAL_SECONDS", 0.5),
                )
                or getattr(Config, "UAC_IPC_POLL_INTERVAL_SECONDS", 0.5)
            ),
        )
        retry_cooldown_s = max(
            0.25,
            float(getattr(Config, "UAC_WATCHDOG_RETRY_COOLDOWN_SECONDS", 1.0) or 1.0),
        )
        next_attempt_at = 0.0

        try:
            while True:
                await asyncio.sleep(poll_interval_s)

                if self._shutdown_event.is_set() or not self.enabled:
                    continue
                if self._transport is None or self._reconnect_in_progress:
                    continue

                now = time.monotonic()
                if now < next_attempt_at:
                    continue

                prompt_state = await asyncio.to_thread(get_uac_prompt_state)
                if not bool(prompt_state.get("likelyPromptActive")):
                    if self._runtime_uac_mode_active:
                        self._set_runtime_uac_mode(
                            False,
                            source="live_watchdog",
                            message="UAC mode cleared. Resuming queued actions.",
                        )
                    continue

                self._set_runtime_uac_mode(
                    True,
                    source="live_watchdog",
                    message="UAC mode active. Waiting for orchestrator to resolve secure desktop prompt.",
                    prompt_state=prompt_state,
                )

                logger.info(
                    "LIVE_UAC_WATCHDOG detected prompt=%s",
                    self._serialize_log_value(prompt_state),
                )
                self.status_received.emit(
                    "UAC prompt detected by always-on detector. Resolving secure-desktop decision..."
                )

                expected_intent = self._uac_expected_intent_summary()

                result = await asyncio.to_thread(
                    self.tools.handle_detected_uac_prompt,
                    source="live_watchdog",
                    expected_intent=expected_intent,
                )
                logger.info(
                    "LIVE_UAC_WATCHDOG result=%s",
                    self._serialize_log_value(result),
                )

                message = str(result.get("message") or "").strip() if isinstance(result, dict) else ""
                if message:
                    if bool(result.get("success")):
                        self.status_received.emit(message)
                    elif bool(result.get("handled")):
                        self.error_received.emit(message)

                if bool(result.get("success")):
                    self._set_runtime_uac_mode(
                        False,
                        source="live_watchdog",
                        message="UAC mode cleared. Resuming queued actions.",
                    )

                next_attempt_at = time.monotonic() + retry_cooldown_s
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live UAC watchdog loop stopped unexpectedly")

    async def _video_loop(self) -> None:
        transport = await self._ensure_session_with_retry()
        if not self._video_stream_enabled:
            return
        interval = max(0.5, 1.0 / max(1, Config.LIVE_VIDEO_FPS))
        try:
            while True:
                try:
                    frame = await asyncio.to_thread(self._capture_video_frame)
                    if frame is not None:
                        await transport.send_video(frame, "image/jpeg")
                except Exception as exc:  # noqa: BLE001
                    if self._is_secure_desktop_capture_error(exc):
                        resumed = await self._pause_video_until_uac_clears()
                        if resumed:
                            await asyncio.sleep(interval)
                            continue
                        return
                    raise
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self._maybe_disable_image_input_for_error(exc):
                if self._should_auto_reconnect():
                    await self._reconnect_with_resume()
                return
            if self._is_recoverable_connection_error(exc):
                logger.warning("Live video loop connection lost; reconnecting: %s", exc)
                if self._should_auto_reconnect():
                    await self._reconnect_with_resume()
                return
            logger.debug("Live video loop stopped: %s", exc, exc_info=True)

    @staticmethod
    def _is_secure_desktop_capture_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        markers = (
            "screen grab failed",
            "openinputdesktop failed",
            "access is denied",
            "secure desktop",
        )
        return any(marker in message for marker in markers)

    async def _pause_video_until_uac_clears(self) -> bool:
        if not self.enabled or self._shutdown_event.is_set():
            return False

        self.status_received.emit("UAC prompt detected. Pausing live video stream until the prompt closes.")
        logger.info("Live video loop paused because secure desktop/UAC is active.")

        timeout_s = max(1.0, float(getattr(Config, "UAC_PROMPT_CLEAR_TIMEOUT_SECONDS", 20.0) or 20.0))
        poll_interval_s = max(0.1, float(getattr(Config, "UAC_IPC_POLL_INTERVAL_SECONDS", 0.5) or 0.5))
        auto_handle_enabled = bool(getattr(Config, "LIVE_UAC_VIDEO_PAUSE_AUTO_HANDLE", True))
        handled_once = False
        deadline = time.monotonic() + timeout_s

        def _resume_after_clear(note: str) -> bool:
            self._set_runtime_uac_mode(
                False,
                source="live_video_pause",
                message="UAC mode cleared. Resuming queued actions.",
            )
            self.status_received.emit(note)
            logger.info("Live video loop resumed after UAC prompt cleared.")
            return True

        while self.enabled and not self._shutdown_event.is_set():
            prompt_state = await asyncio.to_thread(get_uac_prompt_state)
            if not bool(prompt_state.get("likelyPromptActive")):
                return _resume_after_clear("UAC prompt closed. Resuming live video stream.")

            self._set_runtime_uac_mode(
                True,
                source="live_video_pause",
                message="UAC mode active. Waiting for orchestrator to resolve secure desktop prompt.",
                prompt_state=prompt_state,
            )

            if auto_handle_enabled and not handled_once:
                handled_once = True
                logger.info("LIVE_UAC_VIDEO_PAUSE fallback_handler=starting")
                expected_intent = self._uac_expected_intent_summary()
                result = await asyncio.to_thread(
                    self.tools.handle_detected_uac_prompt,
                    source="video_pause_fallback",
                    expected_intent=expected_intent,
                )
                logger.info(
                    "LIVE_UAC_VIDEO_PAUSE fallback_handler_result=%s",
                    self._serialize_log_value(result),
                )
                message = str(result.get("message") or "").strip() if isinstance(result, dict) else ""
                if message:
                    if bool(result.get("success")):
                        self.status_received.emit(message)
                    elif bool(result.get("handled")):
                        self.error_received.emit(message)

                # The fallback handler can block while waiting for prompt resolution;
                # re-check immediately so we do not misclassify a just-cleared prompt as timeout.
                prompt_state = await asyncio.to_thread(get_uac_prompt_state)
                if not bool(prompt_state.get("likelyPromptActive")):
                    return _resume_after_clear("UAC prompt closed. Resuming live video stream.")

            if time.monotonic() >= deadline:
                prompt_state = await asyncio.to_thread(get_uac_prompt_state)
                if not bool(prompt_state.get("likelyPromptActive")):
                    return _resume_after_clear("UAC prompt closed. Resuming live video stream.")

                self.error_received.emit(
                    "UAC prompt did not clear before timeout. Video stream remains paused; audio/tools continue."
                )
                logger.warning("Live video loop timed out waiting for UAC prompt to clear.")
                return False
            await asyncio.sleep(poll_interval_s)

        return False

    async def _microphone_loop(self) -> None:
        await self._ensure_session_with_retry()
        pya = None
        stream = None
        read_failures = 0
        last_user_error_at = 0.0
        stream_io_lock = threading.Lock()

        def _read_stream_chunk(frames_per_buffer: int) -> bytes:
            with stream_io_lock:
                current_stream = stream
                if current_stream is None:
                    return b""
                return current_stream.read(frames_per_buffer, exception_on_overflow=False)

        def _close_stream_handle() -> None:
            nonlocal stream
            with stream_io_lock:
                current_stream = stream
                stream = None
                if current_stream is None:
                    return
                try:
                    stop_stream = getattr(current_stream, "stop_stream", None)
                    if callable(stop_stream):
                        stop_stream()
                except Exception:
                    logger.debug("Failed to stop microphone stream", exc_info=True)
                try:
                    current_stream.close()
                except Exception:
                    logger.debug("Failed to close microphone stream", exc_info=True)

        try:
            pya = pyaudio.PyAudio()
            while self._voice_enabled:
                if stream is None:
                    try:
                        mic_info = pya.get_default_input_device_info()
                        opened_stream = await asyncio.to_thread(
                            pya.open,
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=Config.LIVE_AUDIO_INPUT_RATE,
                            input=True,
                            input_device_index=mic_info["index"],
                            frames_per_buffer=1024,
                        )
                        with stream_io_lock:
                            stream = opened_stream
                        read_failures = 0
                    except Exception as exc:  # noqa: BLE001
                        if self._is_recoverable_connection_error(exc):
                            logger.warning("Microphone stream connection lost; reconnecting: %s", exc)
                            if self._should_auto_reconnect():
                                await self._reconnect_with_resume()
                                continue
                            return
                        now = time.monotonic()
                        if now - last_user_error_at >= 4.0:
                            self.error_received.emit(f"Microphone unavailable, retrying: {exc}")
                            last_user_error_at = now
                        read_failures += 1
                        await asyncio.sleep(min(1.5, 0.1 * read_failures))
                        continue

                try:
                    data = await asyncio.to_thread(_read_stream_chunk, 1024)
                except Exception as exc:  # noqa: BLE001
                    _close_stream_handle()
                    if self._is_recoverable_connection_error(exc):
                        logger.warning("Microphone stream connection lost; reconnecting: %s", exc)
                        if self._should_auto_reconnect():
                            await self._reconnect_with_resume()
                            continue
                        return
                    logger.warning("Microphone read failed; reopening capture stream.", exc_info=True)
                    read_failures += 1
                    await asyncio.sleep(min(1.0, 0.08 * read_failures))
                    continue

                if not data:
                    await asyncio.sleep(0.01)
                    continue

                level = self._compute_audio_level(data)
                if level >= 0.04:
                    self._mark_one_shot_engaged()
                self.audio_level_changed.emit(level)
                if time.monotonic() < self._audio_output_suppressed_until:
                    continue
                try:
                    await self._send_audio_chunk(data)
                except Exception as exc:  # noqa: BLE001
                    if self._is_recoverable_connection_error(exc):
                        logger.warning("Microphone stream connection lost; reconnecting: %s", exc)
                        if self._should_auto_reconnect():
                            await self._reconnect_with_resume()
                            continue
                        return
                    logger.warning("Microphone audio send failed; retrying stream.", exc_info=True)
                    _close_stream_handle()
                    read_failures += 1
                    await asyncio.sleep(min(1.0, 0.08 * read_failures))
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self._is_recoverable_connection_error(exc):
                logger.warning("Microphone stream connection lost; reconnecting: %s", exc)
                if self._should_auto_reconnect():
                    await self._reconnect_with_resume()
                return
            self.error_received.emit(f"Microphone streaming failed: {exc}")
        finally:
            self.audio_level_changed.emit(0.0)
            self.assistant_audio_level_changed.emit(0.0)
            _close_stream_handle()
            try:
                if pya is not None:
                    with stream_io_lock:
                        pya.terminate()
            except Exception:
                logger.debug("Failed to terminate microphone audio handle", exc_info=True)

    async def _send_audio_chunk(self, data: bytes) -> None:
        if not data:
            return
        transport = await self._ensure_session_with_retry()
        await transport.send_audio(
            data,
            f"audio/pcm;rate={Config.LIVE_AUDIO_INPUT_RATE}",
        )

    async def _speaker_loop(self) -> None:
        pya = pyaudio.PyAudio()
        stream = None
        output_rate = Config.LIVE_AUDIO_OUTPUT_RATE
        ratecv_state: Any = None
        pending_chunk: Optional[tuple[bytes, int]] = None
        stream_io_lock = threading.Lock()

        def _write_stream(payload: bytes) -> None:
            with stream_io_lock:
                current_stream = stream
                if current_stream is None:
                    return
                current_stream.write(payload)

        def _close_stream_handle() -> None:
            nonlocal stream
            with stream_io_lock:
                current_stream = stream
                stream = None
                if current_stream is None:
                    return
                try:
                    stop_stream = getattr(current_stream, "stop_stream", None)
                    if callable(stop_stream):
                        stop_stream()
                except Exception:
                    logger.debug("Failed to stop speaker stream", exc_info=True)
                try:
                    current_stream.close()
                except Exception:
                    logger.debug("Failed to close speaker stream", exc_info=True)

        try:
            opened_stream = await asyncio.to_thread(
                pya.open,
                format=pyaudio.paInt16,
                channels=1,
                rate=output_rate,
                output=True,
            )
            with stream_io_lock:
                stream = opened_stream
            while True:
                if self._speaker_queue is None:
                    await asyncio.sleep(0.05)
                    continue
                if pending_chunk is not None:
                    payload, source_rate = pending_chunk
                    pending_chunk = None
                else:
                    payload, source_rate = await self._speaker_queue.get()
                if not payload:
                    continue
                normalized_source_rate = self._normalize_audio_rate(source_rate, output_rate)
                batch_payloads = [payload]
                total_bytes = len(payload)
                queue = self._speaker_queue
                if queue is not None:
                    while (
                        len(batch_payloads) < Config.LIVE_AUDIO_SPEAKER_BATCH_MAX_CHUNKS
                        and total_bytes < Config.LIVE_AUDIO_SPEAKER_BATCH_MAX_BYTES
                    ):
                        try:
                            next_payload, next_rate = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if not next_payload:
                            continue
                        normalized_next_rate = self._normalize_audio_rate(next_rate, output_rate)
                        if normalized_next_rate != normalized_source_rate:
                            pending_chunk = (next_payload, next_rate)
                            break
                        batch_payloads.append(next_payload)
                        total_bytes += len(next_payload)

                merged_payload = (
                    b"".join(batch_payloads) if len(batch_payloads) > 1 else batch_payloads[0]
                )
                out = merged_payload
                if normalized_source_rate != output_rate:
                    try:
                        out, ratecv_state = audioop.ratecv(
                            merged_payload,
                            2,
                            1,
                            normalized_source_rate,
                            output_rate,
                            ratecv_state,
                        )
                        self._maybe_log_audio_resample(normalized_source_rate, output_rate)
                    except Exception:
                        logger.debug("Failed to resample assistant audio chunk", exc_info=True)
                        ratecv_state = None
                        out = payload
                else:
                    ratecv_state = None
                play_seconds = len(out) / float(2 * max(1, output_rate))
                suppress_tail = max(0.0, Config.LIVE_AUDIO_MIC_SUPPRESS_TAIL_MS / 1000.0)
                self._audio_output_suppressed_until = max(
                    self._audio_output_suppressed_until,
                    time.monotonic() + play_seconds + suppress_tail,
                )
                await asyncio.to_thread(_write_stream, out)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("Speaker playback stopped: %s", exc, exc_info=True)
        finally:
            self.assistant_audio_level_changed.emit(0.0)
            _close_stream_handle()
            try:
                with stream_io_lock:
                    pya.terminate()
            except Exception:
                logger.debug("Failed to terminate speaker audio handle", exc_info=True)

    async def _rotation_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if (
                    not self.enabled
                    or self._transport is None
                    or not getattr(self._transport, "should_rotate_sessions", True)
                ):
                    continue
                age = time.monotonic() - self._session_started_at
                if age < Config.LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE:
                    continue
                if self.broker.has_pending():
                    continue
                if not self._should_auto_reconnect():
                    continue
                await self._reconnect_with_resume()
        except asyncio.CancelledError:
            raise

    async def _reconnect_with_resume(self) -> None:
        if not self._should_auto_reconnect():
            return
        if self._reconnect_in_progress:
            return
        self._reconnect_in_progress = True
        self._cancel_go_away_reconnect_task(keep_current=True)
        fragments = self._drain_transcript_buffers(emit_final=True)
        self._resume_pending_user_buffer = fragments["user"]
        self._resume_pending_assistant_buffer = fragments["assistant"]
        reconnect_prompt = self._build_reconnect_prompt(
            user_text=fragments["user"],
            assistant_text=fragments["assistant"],
            goal=self._current_goal,
            recent_action_summary=self._latest_reconnect_action_summary(self._recent_action_updates),
        )
        try:
            await self._disconnect_session(reconnecting=True)
            if self._should_auto_reconnect():
                await self._ensure_session()
                if reconnect_prompt:
                    await self._send_realtime_text(reconnect_prompt, allow_retry=False)
        finally:
            self._resume_pending_user_buffer = ""
            self._resume_pending_assistant_buffer = ""
            self._reconnect_in_progress = False

    async def _recover_from_connection_error(self, exc: Exception, *, context: str) -> bool:
        clean_context = str(context or "live").strip() or "live"
        logger.warning(
            "PixelPilot Live connection dropped during %s; reconnecting: %s",
            clean_context,
            self._format_live_error(exc),
        )
        self.status_received.emit("PixelPilot Live connection dropped. Reconnecting...")
        if not self._should_auto_reconnect():
            return False
        try:
            await self._reconnect_with_resume()
            if self._transport is not None and not self._reconnect_in_progress:
                self.status_received.emit("PixelPilot Live reconnected.")
                return True
        except Exception as reconnect_exc:  # noqa: BLE001
            logger.warning(
                "PixelPilot Live reconnect failed after connection drop: %s",
                self._format_live_error(reconnect_exc),
            )
            logger.debug("Reconnect failure details", exc_info=True)
        message = "PixelPilot Live connection dropped and could not reconnect. Tap to reconnect."
        self._finish_text_turn(error=message)
        self.error_received.emit(message)
        if self.enabled and not self._shutdown_event.is_set():
            await self._disconnect_session(close_client=True)
        return False

    async def _recover_from_invalid_request_error(self, exc: Exception) -> bool:
        if not self._should_auto_reconnect() or self._shutdown_event.is_set():
            return False
        now = time.monotonic()
        if (now - self._last_invalid_request_recovery_at) < 30.0:
            return False
        self._last_invalid_request_recovery_at = now
        if self._reconnect_in_progress:
            return True

        self._reconnect_in_progress = True
        self._cancel_go_away_reconnect_task(keep_current=True)
        self._clear_resume_handle(reason=f"invalid_request_recovery: {exc}")
        fragments = self._drain_transcript_buffers(emit_final=True)
        self._resume_pending_user_buffer = fragments["user"]
        self._resume_pending_assistant_buffer = fragments["assistant"]
        try:
            logger.warning(
                "Live receive loop hit an invalid request; refreshing the session without resumption: %s",
                exc,
            )
            await self._disconnect_session(reconnecting=True)
            if not self._should_auto_reconnect():
                return False
            await self._ensure_session()
            self.status_received.emit("PixelPilot Live refreshed the connection.")
            return bool(self._transport is not None)
        except Exception:
            logger.warning("Fresh recovery after invalid live request failed.", exc_info=True)
            return False
        finally:
            self._resume_pending_user_buffer = ""
            self._resume_pending_assistant_buffer = ""
            self._reconnect_in_progress = False

    def _guidance_desktop_manager(self):
        if str(getattr(self.agent, "active_workspace", "user")).strip().lower() == "agent":
            return getattr(self.agent, "desktop_manager", None)
        return None

    def _guidance_goal_terms(self) -> list[str]:
        getter = getattr(self.agent, "_goal_terms", None)
        if callable(getter):
            try:
                return [str(item).strip() for item in (getter() or []) if str(item).strip()]
            except Exception:
                return []
        return []

    @staticmethod
    def _guidance_snapshot_digest(snapshot: dict[str, Any]) -> dict[str, Any]:
        elements = []
        for item in (snapshot.get("elements") or [])[:8]:
            elements.append(
                {
                    "ui_element_id": item.get("ui_element_id"),
                    "name": item.get("name"),
                    "control_type": item.get("control_type"),
                }
            )

        windows = []
        for item in (snapshot.get("windows") or [])[:5]:
            windows.append(
                {
                    "window_id": item.get("window_id"),
                    "title": item.get("title"),
                    "process_name": item.get("process_name"),
                    "is_visible": item.get("is_visible"),
                }
            )

        return {
            "workspace": snapshot.get("workspace"),
            "active_window_title": snapshot.get("active_window_title"),
            "active_window_class": snapshot.get("active_window_class"),
            "elements_count": snapshot.get("elements_count"),
            "windows_count": snapshot.get("windows_count"),
            "elements_preview": elements,
            "windows_preview": windows,
        }

    async def _guidance_observer_loop(self) -> None:
        # In live guidance mode, detect UIA state changes and proactively nudge the model
        # so it can acknowledge step completion without waiting for explicit "done".
        poll_interval_s = Config.LIVE_GUIDANCE_OBSERVER_POLL_SECONDS
        nudge_cooldown_s = Config.LIVE_GUIDANCE_OBSERVER_NUDGE_COOLDOWN_SECONDS
        try:
            while True:
                await asyncio.sleep(poll_interval_s)

                if self._shutdown_event.is_set() or not self.enabled or self._transport is None:
                    continue
                if not self._is_guidance_mode():
                    continue
                if not str(self._current_goal or "").strip():
                    continue
                if self.broker.has_pending():
                    continue

                try:
                    snapshot = await asyncio.to_thread(
                        ui_automation.get_snapshot,
                        getattr(self.agent, "active_workspace", "user"),
                        self._guidance_desktop_manager(),
                        Config.UIA_MAX_ELEMENTS,
                        self._guidance_goal_terms(),
                    )
                except Exception:
                    continue

                if not isinstance(snapshot, dict) or not snapshot.get("available", False):
                    continue

                signature = ui_automation.snapshot_signature(snapshot)
                if not signature or signature == self._last_guidance_snapshot_signature:
                    continue
                self._last_guidance_snapshot_signature = signature

                summary = self._guidance_snapshot_digest(snapshot)
                self.tools.last_snapshot_summary = summary
                try:
                    self.agent.current_blind_snapshot = snapshot
                except Exception:
                    pass

                now = time.monotonic()
                if now - self._last_guidance_probe_sent_at < nudge_cooldown_s:
                    continue
                self._last_guidance_probe_sent_at = now

                prompt = (
                    "Guidance observer update: screen/UI state changed after the user's action. "
                    "If this indicates the current step was completed, acknowledge it now and give the next step "
                    "without waiting for the user to say done. If not complete, give one short correction. "
                    f"State summary: {json.dumps(summary, ensure_ascii=True)}"
                )
                await self._send_realtime_text(prompt, allow_retry=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Guidance observer loop stopped unexpectedly", exc_info=True)

    def _build_resume_summary(self) -> str:
        pending_user_transcript = (
            str(self._resume_pending_user_buffer or self._user_buffer or "").strip()
        )
        pending_assistant_transcript = (
            str(self._resume_pending_assistant_buffer or self._assistant_buffer or "").strip()
        )
        resume_hint = ""
        if pending_assistant_transcript:
            resume_hint = (
                "Connection was interrupted mid-reply. Continue naturally from the latest assistant "
                "transcript instead of restarting the answer."
            )
        elif pending_user_transcript:
            resume_hint = (
                "Connection was interrupted while the user was speaking. Use the partial user "
                "transcript for continuity, and ask the user to finish only if their request is incomplete."
            )
        payload = {
            "mode": self._mode_key().upper(),
            "goal": self._current_goal,
            "workspace": self._workspace,
            "voice_active": self._voice_enabled,
            "turn_in_progress": bool(
                pending_user_transcript
                or pending_assistant_transcript
                or self._active_text_turn_id is not None
            ),
            "pending_user_transcript": pending_user_transcript,
            "pending_assistant_transcript": pending_assistant_transcript,
            "resume_hint": resume_hint,
            "recent_user_steering": list(self._recent_user_steering),
            "recent_action_updates": list(self._recent_action_updates),
            "last_uia_summary": getattr(self.tools, "last_snapshot_summary", None),
            "last_capture_summary": getattr(self.tools, "last_capture_summary", None),
        }
        clean = {key: value for key, value in payload.items() if value}
        return json.dumps(clean, ensure_ascii=True)

    def _drain_transcript_buffers(self, *, emit_final: bool) -> dict[str, str]:
        fragments = {
            "user": str(self._user_buffer or "").strip(),
            "assistant": str(self._assistant_buffer or "").strip(),
        }
        if emit_final:
            if fragments["user"]:
                self._session_store_call(
                    "record_transcript",
                    "user",
                    fragments["user"],
                    final=True,
                    source="live",
                )
                logger.info(
                    "LIVE_USER_TRANSCRIPT_FINAL text=%s",
                    self._truncate_log_text(fragments["user"]),
                )
                self.transcript_received.emit("user", fragments["user"], True)
            if fragments["assistant"]:
                self._session_store_call(
                    "record_transcript",
                    "assistant",
                    fragments["assistant"],
                    final=True,
                    source="live",
                )
                logger.info(
                    "LIVE_ASSISTANT_RESPONSE_FINAL text=%s",
                    self._truncate_log_text(fragments["assistant"]),
                )
                self.transcript_received.emit("assistant", fragments["assistant"], True)
        self._record_resume_metadata()
        self._user_buffer = ""
        self._assistant_buffer = ""
        return fragments

    @staticmethod
    def _latest_reconnect_action_summary(recent_action_updates: list[dict[str, Any]] | deque[dict[str, Any]]) -> str:
        for payload in reversed(list(recent_action_updates or [])):
            if not isinstance(payload, dict):
                continue
            message = str(payload.get("message") or "").strip()
            name = str(payload.get("name") or "").strip()
            status = str(payload.get("status") or "").strip().lower()
            if message:
                return message
            if name and status:
                return f"{name} ({status})"
            if name:
                return name
            if status:
                return status
        return ""

    def _uac_expected_intent_summary(self) -> str:
        parts: list[str] = []

        goal = str(self._current_goal or "").strip()
        if goal:
            parts.append(f"goal={goal}")

        current_action = self.broker.current_action_payload()
        if isinstance(current_action, dict):
            action_name = str(current_action.get("name") or "").strip()
            action_status = str(current_action.get("status") or "").strip().lower()
            action_args = current_action.get("args")
            if action_name:
                parts.append(f"action={action_name}")
            if action_status:
                parts.append(f"action_status={action_status}")
            if isinstance(action_args, dict) and action_args:
                try:
                    parts.append(f"action_args={json.dumps(action_args, ensure_ascii=True)}")
                except Exception:
                    parts.append("action_args=<unserializable>")

        recent_summary = self._latest_reconnect_action_summary(self._recent_action_updates)
        if recent_summary:
            parts.append(f"recent={recent_summary}")

        if not parts:
            return "No active intent context available from live runtime."

        summary = " | ".join(parts)
        if len(summary) > 900:
            summary = summary[:897] + "..."
        return summary

    @staticmethod
    def _build_reconnect_prompt(
        *,
        user_text: str,
        assistant_text: str,
        goal: str = "",
        recent_action_summary: str = "",
    ) -> str:
        user_fragment = str(user_text or "").strip()
        assistant_fragment = str(assistant_text or "").strip()
        goal_text = str(goal or "").strip()
        action_text = str(recent_action_summary or "").strip()
        continuity_parts = []
        if goal_text:
            continuity_parts.append(f"Active goal: {json.dumps(goal_text, ensure_ascii=True)}")
        if action_text:
            continuity_parts.append(f"Latest action state: {json.dumps(action_text, ensure_ascii=True)}")
        continuity_suffix = f" {' '.join(continuity_parts)}" if continuity_parts else ""
        if assistant_fragment:
            return (
                "Connection resumed in the middle of your reply. Continue the interrupted answer naturally "
                f"from the current context instead of starting over.{continuity_suffix} "
                f"Latest assistant transcript: {json.dumps(assistant_fragment, ensure_ascii=True)}"
            )
        if user_fragment:
            return (
                "Connection resumed while the user was speaking. Use this partial user transcript for "
                "continuity. If the request already seems complete, continue helping. If it seems cut off, "
                f"ask the user to finish the sentence briefly.{continuity_suffix} "
                f"Partial user transcript: {json.dumps(user_fragment, ensure_ascii=True)}"
            )
        if continuity_suffix:
            return (
                "Connection resumed during the active task. Continue from the current context instead of starting over."
                f"{continuity_suffix}"
            )
        return ""

    @staticmethod
    def _compact_action_update(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}

        result = payload.get("result")
        if isinstance(result, dict):
            result = dict(result)

        compact = {
            "action_id": str(payload.get("action_id") or "").strip(),
            "name": str(payload.get("name") or "").strip(),
            "args": payload.get("args"),
            "status": str(payload.get("status") or "").strip(),
            "message": str(payload.get("message") or "").strip(),
            "error": payload.get("error"),
            "result": result,
            "created_at": payload.get("created_at"),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "done": bool(payload.get("done", False)),
        }
        return {
            key: value
            for key, value in compact.items()
            if value not in (None, "", [], {})
        }

    @classmethod
    def _build_action_update_prompt(cls, payload: dict[str, Any]) -> str:
        compact = cls._compact_action_update(payload)
        if not compact:
            return ""
        return (
            "Runtime action update. This is internal execution state, not a new user request. "
            "Do not read it back verbatim to the user, and do not generate a user-facing reply unless "
            "the plan must change or the action failed. Use it as the authoritative outcome of the "
            f"brokered tool call. Action update: {json.dumps(compact, ensure_ascii=True)}"
        )

    def _should_forward_action_update(self, payload: dict[str, Any]) -> bool:
        if not Config.LIVE_FORWARD_ACTION_UPDATES:
            return False
        if not self.enabled or self._transport is None or self._shutdown_event.is_set():
            return False
        if self._reconnect_in_progress:
            return False
        status = str(payload.get("status") or "").strip().lower()
        return status in {"running", "cancel_requested", "succeeded", "failed", "cancelled"}

    def _queue_action_update_prompt(self, payload: dict[str, Any]) -> None:
        if not self._should_forward_action_update(payload):
            return
        prompt = self._build_action_update_prompt(payload)
        if not prompt:
            return
        self._submit_async(
            self._send_realtime_text(prompt, allow_retry=False),
            ensure_loop=False,
        )

    def _on_action_update(self, payload: dict[str, Any]) -> None:
        self._recent_action_updates.append(payload)
        self._session_store_call("record_action_update", payload)
        logger.info(
            "LIVE_ACTION_UPDATE payload=%s",
            self._serialize_log_value(self._compact_action_update(payload)),
        )
        status = str(payload.get("status") or "").strip().lower()
        self._note_typed_turn_activity()
        if status == "queued":
            self.session_state_changed.emit("waiting")
        elif status == "running":
            self.session_state_changed.emit("acting")
        elif status == "cancel_requested":
            self.session_state_changed.emit("interrupted")
        elif self.enabled:
            self.session_state_changed.emit("listening")
        if status in {"succeeded", "failed", "cancelled"}:
            self._schedule_typed_turn_idle_finish(reason=f"action_{status}")
            if str(self._pending_text_nudge or "").strip():
                self._schedule_pending_text_nudge_flush(
                    delay_s=Config.LIVE_TEXT_NUDGE_FLUSH_DELAY_SECONDS,
                    reason=f"action_{status}",
                )
        self._queue_action_update_prompt(payload)
        self._record_resume_metadata()
        self.action_state_changed.emit(payload)

    def _on_capture_ready(self, screenshot_path: str, summary: dict[str, Any]) -> None:
        self._pending_capture_paths.append((screenshot_path, summary))

    async def _send_capture_context(self, screenshot_path: str, summary: dict[str, Any]) -> None:
        if self._transport is None:
            return
        try:
            if self._image_input_enabled:
                with Image.open(screenshot_path) as image:
                    frame = self._image_to_bytes(image, max_size=(1280, 720), fmt="PNG")
                await self._transport.send_video(frame, "image/png")
            await self._send_realtime_text(
                "Detailed capture refreshed for the active workspace. "
                f"Summary: {json.dumps(summary, ensure_ascii=True)}",
                allow_retry=False,
            )
        except Exception as exc:  # noqa: BLE001
            if self._maybe_disable_image_input_for_error(exc):
                if self._should_auto_reconnect():
                    await self._reconnect_with_resume()
                return
            logger.debug("Failed to push capture context: %s", exc, exc_info=True)

    def _capture_video_frame(self) -> Optional[bytes]:
        image = self.agent.screen_capture._capture_raw_image()
        if image is None:
            return None
        return self._image_to_bytes(image, max_size=(640, 360), fmt="JPEG")

    @staticmethod
    def _image_to_bytes(image: Image.Image, *, max_size: tuple[int, int], fmt: str) -> bytes:
        working = image.convert("RGB")
        working.thumbnail(max_size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        save_kwargs: dict[str, Any] = {}
        if fmt.upper() == "JPEG":
            save_kwargs["quality"] = 72
        working.save(buffer, format=fmt, **save_kwargs)
        return buffer.getvalue()

    @staticmethod
    def _parse_args(raw_args: Any) -> dict[str, Any]:
        if raw_args is None:
            return {}
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _compute_audio_level(data: bytes) -> float:
        if not data:
            return 0.0
        try:
            count = len(data) // 2
            samples = struct.unpack(f"{count}h", data)
            rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, count))
            return min(1.0, rms / 10000.0)
        except Exception:
            return 0.0

    @staticmethod
    def _merge_transcript_text(current: str, incoming: str) -> str:
        left = str(current or "").strip()
        right = str(incoming or "").strip()
        if not right:
            return left
        if not left:
            return right
        if right == left:
            return left
        if right.startswith(left):
            return right
        if left.startswith(right):
            return left
        if right in left:
            return left
        if left in right:
            return right

        left_norm = re.sub(r"\s+", " ", left).strip().lower()
        right_norm = re.sub(r"\s+", " ", right).strip().lower()
        if left_norm == right_norm:
            return right

        left_compact = re.sub(r"[\W_]+", "", left_norm)
        right_compact = re.sub(r"[\W_]+", "", right_norm)
        if left_compact and right_compact:
            if left_compact == right_compact:
                return right
            if right_compact.startswith(left_compact):
                return right
            if left_compact.startswith(right_compact):
                return left

        overlap = min(len(left), len(right))
        for size in range(overlap, 0, -1):
            if left.endswith(right[:size]):
                return f"{left}{right[size:]}".strip()

        joiner = "" if left.endswith((" ", "\n")) or right.startswith((" ", "\n")) else " "
        return f"{left}{joiner}{right}".strip()

    @staticmethod
    def _is_recoverable_connection_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        name = exc.__class__.__name__.lower()
        status = getattr(exc, "status", None)
        status_code = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)
        numeric_codes = {
            str(value).strip().lower()
            for value in (status, status_code, code)
            if value is not None
        }
        return (
            bool(numeric_codes.intersection({"1000", "1001", "1005", "1006"}))
            or "connectionclosed" in name
            or "connectionreseterror" in name
            or "timeouterror" in name
            or ("apierror" in name and "1006" in message)
            or "1006 none" in message
            or "abnormal closure" in message
            or "winerror 64" in message
            or "opening handshake" in message
            or "connection reset" in message
            or "network name is no longer available" in message
            or "ping timeout" in message
            or "no close frame received" in message
            or "keepalive ping timeout" in message
            or "sent 1000 (ok)" in message
            or "received 1000 (ok)" in message
            or message in {"1000 none", "1000 none."}
            or "session is not connected" in message
            or "live session is not connected" in message
            or "backend session is not connected" in message
        )

    @staticmethod
    def _format_live_error(exc: Exception) -> str:
        message = str(exc or "").strip()
        if not message:
            message = exc.__class__.__name__
        message = re.sub(r"\s+", " ", message)
        return message[:240]

    @staticmethod
    def _is_nonrecoverable_request_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        status = getattr(exc, "status", None)
        status_code = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)

        numeric_codes = {
            str(value).strip().lower()
            for value in (status, status_code, code)
            if value is not None
        }
        if "1007" in numeric_codes:
            return True

        return (
            "1007 none" in message
            or "received 1007" in message
            or "sent 1007" in message
            or "invalid frame payload data" in message
            or "request contains an invalid argument" in message
            or "request contains invalid argument" in message
        )

    async def _enqueue_speaker_audio(self, data: bytes, sample_rate: int) -> None:
        queue = self._speaker_queue
        if queue is None or not data:
            return
        item = (data, self._normalize_audio_rate(sample_rate, Config.LIVE_AUDIO_OUTPUT_RATE))

        if Config.LIVE_AUDIO_LOSSLESS_MODE:
            if queue.maxsize > 0 and queue.full():
                now = time.monotonic()
                if (
                    now - self._speaker_backpressure_logged_at
                    > Config.LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_COOLDOWN_SECONDS
                ):
                    logger.warning(
                        "Live speaker queue full in lossless mode (qsize=%d/%d); applying backpressure",
                        queue.qsize(),
                        queue.maxsize,
                    )
                    self._speaker_backpressure_logged_at = now
            await queue.put(item)
            backlog = queue.qsize()
            warning_threshold = Config.LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_CHUNKS
            if queue.maxsize > 0:
                warning_threshold = min(warning_threshold, queue.maxsize)
            if backlog >= max(1, warning_threshold):
                now = time.monotonic()
                if (
                    now - self._speaker_backlog_logged_at
                    > Config.LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_COOLDOWN_SECONDS
                ):
                    if queue.maxsize > 0:
                        logger.warning(
                            "Live speaker backlog high in lossless mode (qsize=%d/%d)",
                            backlog,
                            queue.maxsize,
                        )
                    else:
                        logger.warning(
                            "Live speaker backlog high in lossless mode (qsize=%d)",
                            backlog,
                        )
                    self._speaker_backlog_logged_at = now
            return

        if not queue.full():
            await queue.put(item)
            return

        try:
            await asyncio.wait_for(
                queue.put(item),
                timeout=Config.LIVE_AUDIO_QUEUE_PUT_TIMEOUT_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            pass

        maxsize = queue.maxsize if queue.maxsize > 0 else Config.LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS
        trim_to = min(
            maxsize - 1,
            max(Config.LIVE_AUDIO_SPEAKER_QUEUE_TRIM_TO_CHUNKS, maxsize - 16),
        )
        dropped = 0
        while queue.qsize() > trim_to:
            try:
                queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break

        if dropped:
            now = time.monotonic()
            if now - self._speaker_drop_logged_at > Config.LIVE_AUDIO_QUEUE_DROP_LOG_COOLDOWN_SECONDS:
                logger.warning(
                    "Live speaker queue pressure: dropped %d stale chunks (qsize=%d/%d)",
                    dropped,
                    queue.qsize(),
                    maxsize,
                )
                self._speaker_drop_logged_at = now

        await queue.put(item)

    def _clear_speaker_queue(self) -> None:
        queue = self._speaker_queue
        if queue is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    @staticmethod
    def _extract_audio_rate(mime_type: str) -> int:
        fallback = Config.LIVE_AUDIO_OUTPUT_RATE
        text = str(mime_type or "").lower()
        if not text:
            return fallback
        match = re.search(r"(?:rate|sample_rate)\s*=\s*(\d{4,6})", text)
        if not match:
            return fallback
        return LiveSessionManager._normalize_audio_rate(match.group(1), fallback)

    @staticmethod
    def _normalize_audio_rate(rate: Any, fallback: int) -> int:
        try:
            parsed = int(str(rate).strip())
        except Exception:
            return fallback
        return parsed if 8000 <= parsed <= 96000 else fallback

    def _maybe_log_audio_resample(self, src_rate: int, dst_rate: int) -> None:
        now = time.monotonic()
        if now - self._audio_resample_logged_at < Config.LIVE_AUDIO_RESAMPLE_LOG_COOLDOWN_SECONDS:
            return
        logger.info("Resampling assistant audio from %s Hz to %s Hz", src_rate, dst_rate)
        self._audio_resample_logged_at = now

    def _maybe_disable_image_input_for_error(self, exc: Exception) -> bool:
        if not self._image_input_enabled:
            return False
        message = str(exc or "").lower()
        if "operation is not implemented" not in message and "not supported" not in message:
            return False
        self._image_input_enabled = False
        self._video_stream_enabled = False
        logger.warning(
            "PixelPilot Live model rejected image/video input; disabling image stream for this run."
        )
        self.error_received.emit(
            "Live model rejected screen image/video input; continuing with audio and tool context only."
        )
        return True
