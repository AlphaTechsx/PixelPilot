from __future__ import annotations

import audioop
import asyncio
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
from .broker import LiveActionBroker
from .transports import (
    BaseLiveTransport,
    BackendGeminiLiveTransport,
    DirectGeminiLiveTransport,
)
from .tools import LiveToolRegistry
from tools import ui_automation

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
        self._shutdown_event = threading.Event()
        self._assistant_buffer = ""
        self._user_buffer = ""
        self._current_goal = ""
        self._recent_user_steering: deque[str] = deque(maxlen=6)
        self._recent_action_updates: deque[dict[str, Any]] = deque(maxlen=12)
        self._pending_capture_paths: deque[tuple[str, dict[str, Any]]] = deque(maxlen=4)
        self._audio_output_suppressed_until = 0.0
        self._reconnect_in_progress = False
        self._image_input_enabled = bool(Config.LIVE_ENABLE_IMAGE_INPUT)
        self._video_stream_enabled = bool(Config.LIVE_ENABLE_VIDEO_STREAM and self._image_input_enabled)
        self._speaker_drop_logged_at = 0.0
        self._speaker_backlog_logged_at = 0.0
        self._speaker_backpressure_logged_at = 0.0
        self._audio_resample_logged_at = 0.0
        self._last_guidance_snapshot_signature = ""
        self._last_guidance_probe_sent_at = 0.0
        self._turn_state_lock = threading.Lock()
        self._active_text_turn_id: Optional[int] = None
        self._next_text_turn_id = 0
        self._turn_waiters: dict[int, dict[str, Any]] = {}
        self._last_typed_turn_activity_at = 0.0
        self._typed_turn_idle_finish_timer: Optional[threading.Timer] = None
        self._typed_turn_idle_finish_generation = 0
        self._pending_text_nudge = ""
        self._pending_text_nudge_timer: Optional[threading.Timer] = None
        self._pending_text_nudge_generation = 0
        self._soft_interrupt_requested = False

        self.broker = LiveActionBroker(on_action_update=self._on_action_update)
        self.tools = LiveToolRegistry(
            agent=agent,
            broker=self.broker,
            on_capture_ready=self._on_capture_ready,
        )
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self.availability_changed.emit(self.is_available, self.unavailable_reason)

    def _mode_key(self, mode: Optional[object] = None) -> str:
        value = self._mode if mode is None else mode
        if isinstance(value, OperationMode):
            return value.value
        enum_value = getattr(value, "value", value)
        return str(enum_value or "").strip().lower()

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

    def _transport_cls(self):
        return DirectGeminiLiveTransport if Config.USE_DIRECT_API else BackendGeminiLiveTransport

    def _create_transport(self) -> BaseLiveTransport:
        cls = self._transport_cls()
        if self._transport is None or not isinstance(self._transport, cls):
            self._transport = cls()
        return self._transport

    @property
    def is_available(self) -> bool:
        if not Config.ENABLE_GEMINI_LIVE_MODE:
            return False
        transport_cls = self._transport_cls()
        return bool(transport_cls.is_supported())

    @property
    def unavailable_reason(self) -> str:
        if not Config.ENABLE_GEMINI_LIVE_MODE:
            return "Live mode is disabled by config."
        transport_cls = self._transport_cls()
        return transport_cls.unavailable_reason()

    @property
    def voice_enabled(self) -> bool:
        return self._voice_enabled

    def set_enabled(self, enabled: bool) -> bool:
        target = bool(enabled)
        if target and not self.is_available:
            self.error_received.emit(self.unavailable_reason)
            return False
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self.enabled = target
        if not target:
            self.stop_voice()
            self._cancel_typed_turn_idle_finish_timer()
            self._clear_pending_text_nudge()
            self._finish_text_turn(error="AI power was turned off.")
            self._submit_async(self._disconnect_session(close_client=True), ensure_loop=False)
            self.session_state_changed.emit("disconnected")
        return True

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
            return None, "AI power is off."
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
            self.error_received.emit("Failed to send the latest steering update to Gemini Live.")
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

        self._finish_text_turn(error="Failed to send the latest steering update to Gemini Live.")
        self._restore_pending_text_nudge(pending_text)
        self.error_received.emit("Failed to send the latest steering update to Gemini Live.")
        return False

    def _handle_text_nudge_submission(self, text: str) -> dict[str, Any]:
        clean = self._record_user_steering(text)
        if not clean:
            return {
                "ok": False,
                "status": "rejected",
                "message": "Message is empty.",
            }

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
        waiter, error_message = self._begin_text_turn(text, wait_for_result=False)
        if waiter is None:
            clean = str(text or "").strip()
            if error_message == "Wait for the current reply before sending another command.":
                return self._handle_text_nudge_submission(clean)
            if error_message:
                self.error_received.emit(error_message)
            return {
                "ok": False,
                "status": "rejected",
                "message": error_message or "Unable to submit command.",
            }
        submitted = self._submit_async(self._send_text(str(waiter["submitted_text"])))
        if not submitted:
            self._finish_text_turn(error="Failed to send the command to Gemini Live.")
            self.error_received.emit("Failed to send the command to Gemini Live.")
            return {
                "ok": False,
                "status": "submit_failed",
                "message": "Failed to send the command to Gemini Live.",
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

        submitted = self._submit_async(self._send_text(str(waiter["submitted_text"])))
        if not submitted:
            self._finish_text_turn(error="Failed to send the command to Gemini Live.")
            return {
                "ok": False,
                "error": "submit_failed",
                "message": "Failed to send the command to Gemini Live.",
                "text": "",
            }

        timeout_value = max(1.0, float(timeout_s or 90.0))
        event = waiter.get("event")
        if event is None or not event.wait(timeout=timeout_value):
            self._finish_text_turn(error="Timed out waiting for Gemini Live to finish the turn.")
            return {
                "ok": False,
                "error": "timeout",
                "message": "Timed out waiting for Gemini Live to finish the turn.",
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

    def start_voice(self) -> bool:
        if not self.enabled:
            self.error_received.emit("Enable Live mode before starting voice.")
            return False
        clear_stop = getattr(self.agent, "clear_stop_request", None)
        if callable(clear_stop):
            try:
                clear_stop()
            except Exception:
                pass
        if self._voice_enabled:
            return True
        submitted = self._submit_async(self._start_voice_async())
        if not submitted:
            message = str(getattr(self, "unavailable_reason", "") or "").strip()
            if not message:
                message = (
                    "Gemini Live voice could not start because the background session was unavailable."
                )
            self.error_received.emit(message)
            return False
        self._voice_enabled = True
        self.voice_active_changed.emit(True)
        self.session_state_changed.emit("connecting")
        return True

    def stop_voice(self) -> bool:
        self._voice_enabled = False
        self._cancel_typed_turn_idle_finish_timer()
        self._cancel_pending_text_nudge_timer()
        self.voice_active_changed.emit(False)
        return self._submit_async(self._stop_voice_async(), ensure_loop=False)

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

    def notify_mode_changed(self, mode: object) -> None:
        self._mode = mode
        self.tools.set_guidance_mode(self._is_guidance_mode())
        if self.enabled and self._transport is not None:
            # Force a fresh persona/system prompt when the runtime mode changes.
            self._resume_handle = None
            self._submit_async(self._reconnect_with_resume(), ensure_loop=False)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._cancel_typed_turn_idle_finish_timer()
        self._clear_pending_text_nudge()
        self._finish_text_turn(error="Gemini Live session shut down.")
        self._voice_enabled = False
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
            self._finish_text_turn(error=f"Gemini Live background task failed: {exc}")
            self.error_received.emit(f"Gemini Live background task failed: {exc}")

    async def _send_text(self, text: str) -> None:
        await self._send_realtime_text(str(text or ""))

    async def _send_realtime_text(self, text: str, *, allow_retry: bool = True) -> None:
        payload = str(text or "").strip()
        if not payload:
            return

        transport = await self._ensure_session_with_retry()
        try:
            await asyncio.wait_for(
                transport.send_text(payload),
                timeout=Config.LIVE_TEXT_SEND_TIMEOUT_SECONDS,
            )
        except RateLimitError:
            raise
        except asyncio.TimeoutError as exc:
            if allow_retry and self.enabled and not self._shutdown_event.is_set():
                logger.warning("Timed out sending realtime text; reconnecting live session.")
                await self._reconnect_with_resume()
                await self._send_realtime_text(payload, allow_retry=False)
                return
            raise RuntimeError("Timed out sending text to Gemini Live session.") from exc
        except Exception:
            if allow_retry and self.enabled and not self._shutdown_event.is_set():
                logger.warning("Realtime text send failed; reconnecting live session.", exc_info=True)
                await self._reconnect_with_resume()
                await self._send_realtime_text(payload, allow_retry=False)
                return
            raise

    async def _start_voice_async(self) -> None:
        try:
            await self._ensure_session_with_retry()
            if self._mic_task and not self._mic_task.done():
                return
            self._mic_task = asyncio.create_task(self._microphone_loop())
            self.session_state_changed.emit("listening")
        except Exception:
            if self._voice_enabled:
                self._voice_enabled = False
                self.voice_active_changed.emit(False)
            raise

    async def _ensure_session_with_retry(self, retries: int = 1):
        attempt = 0
        while True:
            try:
                return await self._ensure_session()
            except Exception as exc:  # noqa: BLE001
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

    async def _stop_voice_async(self) -> None:
        if self._mic_task and not self._mic_task.done():
            self._mic_task.cancel()
            await asyncio.gather(self._mic_task, return_exceptions=True)
        self._mic_task = None
        self.audio_level_changed.emit(0.0)
        if self.enabled:
            self.session_state_changed.emit("listening")

    async def _ensure_session(self):
        if self._transport is not None:
            return self._transport

        self.session_state_changed.emit("connecting")
        transport = self._create_transport()
        config = self._build_connect_config()
        await transport.connect(model=Config.GEMINI_LIVE_MODEL, config=config)
        self._session_started_at = time.monotonic()
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
        if self._is_guidance_mode():
            self._last_guidance_snapshot_signature = ""
            self._last_guidance_probe_sent_at = 0.0
            self._guidance_observer_task = asyncio.create_task(self._guidance_observer_loop())
        if self._voice_enabled and (self._mic_task is None or self._mic_task.done()):
            self._mic_task = asyncio.create_task(self._microphone_loop())
        self.session_state_changed.emit("listening")

        return transport

    async def _disconnect_session(
        self,
        *,
        close_client: bool = False,
        reconnecting: bool = False,
    ) -> None:
        current_task = asyncio.current_task()
        tasks = [
            self._mic_task,
            self._video_task,
            self._speaker_task,
            self._receive_task,
            self._rotation_task,
            self._guidance_observer_task,
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
        else:
            self.session_state_changed.emit("disconnected")

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
        if types is not None and hasattr(types, "MediaResolution"):
            media_resolution = getattr(types.MediaResolution, "MEDIA_RESOLUTION_LOW", None)
            config["media_resolution"] = (
                media_resolution if Config.USE_DIRECT_API and media_resolution is not None else "MEDIA_RESOLUTION_LOW"
            )
        else:
            config["media_resolution"] = "MEDIA_RESOLUTION_LOW"
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
                        handle = update.get("handle")
                        if handle:
                            self._resume_handle = str(handle)

                    tool_call = response.get("tool_call")
                    if tool_call:
                        self.session_state_changed.emit("acting")
                        await self._handle_tool_call(tool_call)

                    server_content = response.get("server_content")
                    if not isinstance(server_content, dict):
                        continue

                    input_transcription = server_content.get("input_transcription")
                    if isinstance(input_transcription, dict):
                        text = str(input_transcription.get("text") or "")
                        if text:
                            self._user_buffer = self._merge_transcript_text(self._user_buffer, text)
                            self.transcript_received.emit("user", self._user_buffer, False)

                    output_text = ""
                    output_transcription = server_content.get("output_transcription")
                    if isinstance(output_transcription, dict):
                        output_text = str(output_transcription.get("text") or "")
                        if output_text:
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
                            if (
                                part_text
                                and not has_output_transcription
                                and not bool(part.get("thought"))
                            ):
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
                        self._finish_text_turn(error="Gemini Live interrupted the current turn.")
                        self._drain_transcript_buffers(emit_final=True)
                        self.assistant_audio_level_changed.emit(0.0)
                        self._clear_speaker_queue()
                        pending_audio_chunks.clear()

                    if bool(server_content.get("turn_complete")):
                        assistant_text = str(self._assistant_buffer or "").strip()
                        self._drain_transcript_buffers(emit_final=True)
                        self._finish_text_turn(assistant_text=assistant_text)
                        self.assistant_audio_level_changed.emit(0.0)
                        self.session_state_changed.emit("listening")

                    for payload, sample_rate in pending_audio_chunks:
                        self._audio_output_suppressed_until = time.monotonic() + 0.25
                        self.assistant_audio_level_changed.emit(
                            self._compute_audio_level(payload)
                        )
                        await self._enqueue_speaker_audio(payload, sample_rate)

                if not received_messages:
                    break
        except asyncio.CancelledError:
            raise
        except RateLimitError as exc:
            logger.warning("Live receive loop rate limited: %s", exc)
            self._finish_text_turn(error=str(exc))
            self.error_received.emit(str(exc))
            if self.enabled and not self._shutdown_event.is_set():
                await self._disconnect_session()
            return
        except Exception as exc:  # noqa: BLE001
            if self._maybe_disable_image_input_for_error(exc):
                if self.enabled and not self._shutdown_event.is_set():
                    await self._reconnect_with_resume()
                return
            if self._is_recoverable_connection_error(exc):
                logger.warning("Live receive loop connection lost; reconnecting: %s", exc)
                if self.enabled and not self._shutdown_event.is_set():
                    await self._reconnect_with_resume()
                return
            logger.exception("Live receive loop failed")
            self._finish_text_turn(error=f"Live session error: {exc}")
            self.error_received.emit(f"Live session error: {exc}")
            if self.enabled and not self._shutdown_event.is_set():
                await self._reconnect_with_resume()

    async def _handle_tool_call(self, tool_call: Any) -> None:
        responses = []
        function_calls = []
        if isinstance(tool_call, dict):
            function_calls = tool_call.get("function_calls") or []
        for function_call in function_calls:
            if not isinstance(function_call, dict):
                continue
            args = self._parse_args(function_call.get("args"))
            if not args:
                args = self._parse_args(function_call.get("arguments"))
            result = await asyncio.to_thread(
                self.tools.execute,
                str(function_call.get("name") or ""),
                args,
            )
            responses.append(
                {
                    "id": function_call.get("id"),
                    "name": str(function_call.get("name") or ""),
                    "response": {"result": result},
                }
            )

        if responses and self._transport is not None:
            await self._transport.send_tool_responses(responses)

        while self._pending_capture_paths and self._transport is not None:
            path, summary = self._pending_capture_paths.popleft()
            await self._send_capture_context(path, summary)

    async def _video_loop(self) -> None:
        transport = await self._ensure_session_with_retry()
        if not self._video_stream_enabled:
            return
        interval = max(0.5, 1.0 / max(1, Config.LIVE_VIDEO_FPS))
        try:
            while True:
                frame = await asyncio.to_thread(self._capture_video_frame)
                if frame is not None:
                    await transport.send_video(frame, "image/jpeg")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self._maybe_disable_image_input_for_error(exc):
                if self.enabled and not self._shutdown_event.is_set():
                    await self._reconnect_with_resume()
                return
            if self._is_recoverable_connection_error(exc):
                logger.warning("Live video loop connection lost; reconnecting: %s", exc)
                if self.enabled and not self._shutdown_event.is_set():
                    await self._reconnect_with_resume()
                return
            logger.debug("Live video loop stopped: %s", exc, exc_info=True)

    async def _microphone_loop(self) -> None:
        await self._ensure_session_with_retry()
        pya = pyaudio.PyAudio()
        stream = None
        try:
            mic_info = pya.get_default_input_device_info()
            stream = await asyncio.to_thread(
                pya.open,
                format=pyaudio.paInt16,
                channels=1,
                rate=Config.LIVE_AUDIO_INPUT_RATE,
                input=True,
                input_device_index=mic_info["index"],
                frames_per_buffer=1024,
            )
            while self._voice_enabled:
                data = await asyncio.to_thread(stream.read, 1024, exception_on_overflow=False)
                self.audio_level_changed.emit(self._compute_audio_level(data))
                if time.monotonic() < self._audio_output_suppressed_until:
                    continue
                await self._send_audio_chunk(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self._is_recoverable_connection_error(exc):
                logger.warning("Microphone stream connection lost; reconnecting: %s", exc)
                if self.enabled and not self._shutdown_event.is_set():
                    await self._reconnect_with_resume()
                return
            self.error_received.emit(f"Microphone streaming failed: {exc}")
        finally:
            self.audio_level_changed.emit(0.0)
            self.assistant_audio_level_changed.emit(0.0)
            if stream is not None:
                await asyncio.to_thread(stream.close)
            pya.terminate()

    async def _send_audio_chunk(self, data: bytes) -> None:
        if self._transport is None or not data:
            return
        await self._transport.send_audio(
            data,
            f"audio/pcm;rate={Config.LIVE_AUDIO_INPUT_RATE}",
        )

    async def _speaker_loop(self) -> None:
        pya = pyaudio.PyAudio()
        stream = None
        output_rate = Config.LIVE_AUDIO_OUTPUT_RATE
        ratecv_state: Any = None
        pending_chunk: Optional[tuple[bytes, int]] = None
        try:
            stream = await asyncio.to_thread(
                pya.open,
                format=pyaudio.paInt16,
                channels=1,
                rate=output_rate,
                output=True,
            )
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
                await asyncio.to_thread(stream.write, out)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("Speaker playback stopped: %s", exc, exc_info=True)
        finally:
            self.assistant_audio_level_changed.emit(0.0)
            if stream is not None:
                await asyncio.to_thread(stream.close)
            pya.terminate()

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
                await self._reconnect_with_resume()
        except asyncio.CancelledError:
            raise

    async def _reconnect_with_resume(self) -> None:
        if self._shutdown_event.is_set():
            return
        if self._reconnect_in_progress:
            return
        self._reconnect_in_progress = True
        fragments = self._drain_transcript_buffers(emit_final=True)
        self._resume_pending_user_buffer = fragments["user"]
        self._resume_pending_assistant_buffer = fragments["assistant"]
        reconnect_prompt = self._build_reconnect_prompt(
            user_text=fragments["user"],
            assistant_text=fragments["assistant"],
        )
        try:
            await self._disconnect_session(reconnecting=True)
            if self.enabled and not self._shutdown_event.is_set():
                await self._ensure_session()
                if reconnect_prompt:
                    await self._send_realtime_text(reconnect_prompt, allow_retry=False)
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
            "last_uia_summary": self.tools.last_snapshot_summary,
            "last_capture_summary": self.tools.last_capture_summary,
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
                self.transcript_received.emit("user", fragments["user"], True)
            if fragments["assistant"]:
                self.transcript_received.emit("assistant", fragments["assistant"], True)
        self._user_buffer = ""
        self._assistant_buffer = ""
        return fragments

    @staticmethod
    def _build_reconnect_prompt(*, user_text: str, assistant_text: str) -> str:
        user_fragment = str(user_text or "").strip()
        assistant_fragment = str(assistant_text or "").strip()
        if assistant_fragment:
            return (
                "Connection resumed in the middle of your reply. Continue the interrupted answer naturally "
                "from the current context instead of starting over. "
                f"Latest assistant transcript: {json.dumps(assistant_fragment, ensure_ascii=True)}"
            )
        if user_fragment:
            return (
                "Connection resumed while the user was speaking. Use this partial user transcript for "
                "continuity. If the request already seems complete, continue helping. If it seems cut off, "
                "ask the user to finish the sentence briefly. "
                f"Partial user transcript: {json.dumps(user_fragment, ensure_ascii=True)}"
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
                if self.enabled and not self._shutdown_event.is_set():
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
        return (
            "connectionclosed" in name
            or "connectionreseterror" in name
            or "timeouterror" in name
            or "winerror 64" in message
            or "opening handshake" in message
            or "connection reset" in message
            or "network name is no longer available" in message
            or "ping timeout" in message
            or "no close frame received" in message
            or "keepalive ping timeout" in message
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
            "Gemini Live model rejected image/video input; disabling image stream for this run."
        )
        self.error_received.emit(
            "Live model rejected screen image/video input; continuing with audio and tool context only."
        )
        return True
