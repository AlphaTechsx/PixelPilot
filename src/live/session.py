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

from config import Config, OperationMode
from .broker import LiveActionBroker
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


LIVE_SYSTEM_INSTRUCTION = (
    "You are Pixy operating in Gemini Live mode on a Windows PC. "
    "When users ask to explain, teach, or help them understand what is on screen, you must annotate while explaining. "
    "Do not wait for an explicit 'highlight' request. "
    "For those requests, call overlay drawing tools "
    "(overlay_draw_box, overlay_draw_text, overlay_draw_pointer, overlay_clear) by default. "
    "Use tight, precise highlights around the smallest relevant element and avoid broad container boxes. "
    "Keep annotations temporary: set ttl_ms for boxes/text/pointers and clear stale overlays when the explanation segment is done. "
    "Work UIA-first: prefer UI Automation state, window listing, window focus, keyboard actions, "
    "app launch, and brokered status checks before requesting detailed vision. "
    "Treat the low-FPS video feed as coarse awareness only, not precise click targeting. "
    "Use capture_screen only when UI Automation or coarse live video is insufficient. "
    "Never issue a second mutating tool call while any action is queued, running, or cancel_requested. "
    "After a mutating tool call, inspect get_action_status or wait_for_action before planning the next action. "
    "Respect the current workspace, ask for confirmation before destructive actions, and keep replies concise."
)

LIVE_SYSTEM_CONTEXT_PREFIX = (
    "Runtime continuity context. This is state, not a fresh user request. "
    "Use it only to preserve continuity across reconnects and turns."
)


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
        self._client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._session = None
        self._session_cm = None
        self._session_started_at = 0.0
        self._resume_handle: Optional[str] = None
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
        self._audio_resample_logged_at = 0.0
        self._last_guidance_snapshot_signature = ""
        self._last_guidance_probe_sent_at = 0.0

        self.broker = LiveActionBroker(on_action_update=self._on_action_update)
        self.tools = LiveToolRegistry(
            agent=agent,
            broker=self.broker,
            on_capture_ready=self._on_capture_ready,
        )
        self.tools.set_guidance_mode(self._is_guidance_mode())
        self.availability_changed.emit(self.is_available, self.unavailable_reason)

    @property
    def is_available(self) -> bool:
        return bool(Config.LIVE_MODE_AVAILABLE and genai is not None and types is not None)

    @property
    def unavailable_reason(self) -> str:
        if not Config.ENABLE_GEMINI_LIVE_MODE:
            return "Live mode is disabled by config."
        if not Config.USE_DIRECT_API:
            return "Live mode requires direct Gemini API access."
        if genai is None or types is None:
            return f"Gemini Live dependencies unavailable: {_IMPORT_ERROR}"
        return ""

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
            self._submit_async(self._disconnect_session(close_client=True), ensure_loop=False)
            self.session_state_changed.emit("disconnected")
        return True

    def submit_text(self, text: str) -> bool:
        clean = str(text or "").strip()
        if not clean:
            return False
        if not self.enabled:
            self.error_received.emit("Live mode is disabled.")
            return False

        if not self._current_goal:
            self._current_goal = clean
        self.agent.current_task = clean
        self._recent_user_steering.append(clean)
        if self._looks_like_explanation_request(clean):
            self._clear_overlay_immediately()
        self.session_state_changed.emit("thinking")
        return self._submit_async(self._send_text(clean))

    def start_voice(self) -> bool:
        if not self.enabled:
            self.error_received.emit("Enable Live mode before starting voice.")
            return False
        self._voice_enabled = True
        self.voice_active_changed.emit(True)
        self.session_state_changed.emit("connecting")
        return self._submit_async(self._start_voice_async())

    def stop_voice(self) -> bool:
        self._voice_enabled = False
        self.voice_active_changed.emit(False)
        return self._submit_async(self._stop_voice_async(), ensure_loop=False)

    def request_stop(self) -> None:
        self.broker.cancel_current_action("Stop requested. Finish at a safe boundary.")
        if self.enabled and self._session is not None:
            self.session_state_changed.emit("interrupted")
            self._submit_async(
                self._send_text(
                    "Stop the current plan at the next safe boundary and wait for new instructions."
                ),
                ensure_loop=False,
            )

    def notify_workspace_changed(self, workspace: str) -> None:
        self._workspace = (workspace or "user").strip().lower() or "user"

    def shutdown(self) -> None:
        self._shutdown_event.set()
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
        except Exception as exc:  # noqa: BLE001
            logger.exception("Live background task failed")
            self.error_received.emit(f"Gemini Live background task failed: {exc}")

    async def _send_text(self, text: str) -> None:
        await self._send_realtime_text(str(text or ""))

    async def _send_realtime_text(self, text: str, *, allow_retry: bool = True) -> None:
        payload = str(text or "").strip()
        if not payload:
            return

        session = await self._ensure_session_with_retry()
        try:
            await asyncio.wait_for(session.send_realtime_input(text=payload), timeout=8.0)
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
        await self._ensure_session_with_retry()
        if self._mic_task and not self._mic_task.done():
            return
        self._mic_task = asyncio.create_task(self._microphone_loop())
        self.session_state_changed.emit("listening")

    async def _ensure_session_with_retry(self, retries: int = 1):
        attempt = 0
        while True:
            try:
                return await self._ensure_session()
            except Exception as exc:  # noqa: BLE001
                if attempt >= retries or not self._is_recoverable_connection_error(exc):
                    raise
                delay_s = 0.75 * (attempt + 1)
                logger.warning(
                    "Live session connect failed (%s); retrying in %.2fs",
                    exc,
                    delay_s,
                )
                await self._disconnect_session()
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
        if self._session is not None:
            return self._session

        self.session_state_changed.emit("connecting")
        if self._client is None:
            self._client = genai.Client(api_key=Config.GEMINI_API_KEY)

        config = self._build_connect_config()
        self._session_cm = self._client.aio.live.connect(model=Config.GEMINI_LIVE_MODEL, config=config)
        self._session = await self._session_cm.__aenter__()
        self._session_started_at = time.monotonic()
        self._speaker_queue = asyncio.Queue(maxsize=Config.LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS)
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

        return self._session

    async def _disconnect_session(self, *, close_client: bool = False) -> None:
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

        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("Failed to close live session", exc_info=True)
        self._session_cm = None
        self._session = None
        if close_client and self._client is not None:
            try:
                await self._client.aio.aclose()
            except Exception:
                logger.debug("Failed to close Gemini client", exc_info=True)
            self._client = None
        if not self.enabled or self._shutdown_event.is_set():
            self.session_state_changed.emit("disconnected")
        else:
            self.session_state_changed.emit("connecting")

    def _build_connect_config(self) -> dict[str, Any]:
        guidance_mode = self._is_guidance_mode()
        system_instruction = (
            LIVE_GUIDANCE_SYSTEM_INSTRUCTION if guidance_mode else LIVE_SYSTEM_INSTRUCTION
        )
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
        if hasattr(types, "MediaResolution"):
            config["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_LOW
        if hasattr(types, "SessionResumptionConfig"):
            try:
                if self._resume_handle:
                    config["session_resumption"] = types.SessionResumptionConfig(handle=self._resume_handle)
                else:
                    config["session_resumption"] = types.SessionResumptionConfig()
            except TypeError:
                config["session_resumption"] = {"handle": self._resume_handle} if self._resume_handle else {}
        return config

    async def _receive_loop(self) -> None:
        assert self._session is not None
        try:
            while not self._shutdown_event.is_set():
                received_messages = False
                async for response in self._session.receive():
                    received_messages = True
                    if self._shutdown_event.is_set():
                        break

                    if getattr(response, "session_resumption_update", None):
                        update = response.session_resumption_update
                        handle = getattr(update, "new_handle", None) or getattr(update, "resumption_handle", None)
                        if handle:
                            self._resume_handle = str(handle)

                    tool_call = getattr(response, "tool_call", None)
                    if tool_call:
                        self.session_state_changed.emit("acting")
                        await self._handle_tool_call(tool_call)

                    server_content = getattr(response, "server_content", None)
                    if not server_content:
                        continue

                    if getattr(server_content, "input_transcription", None):
                        text = str(getattr(server_content.input_transcription, "text", "") or "")
                        if text:
                            self._user_buffer = self._merge_transcript_text(self._user_buffer, text)
                            self.transcript_received.emit("user", self._user_buffer, False)

                    output_text = ""
                    if getattr(server_content, "output_transcription", None):
                        output_text = str(getattr(server_content.output_transcription, "text", "") or "")
                        if output_text:
                            self._assistant_buffer = self._merge_transcript_text(self._assistant_buffer, output_text)
                            self.transcript_received.emit("assistant", self._assistant_buffer, False)

                    model_turn = getattr(server_content, "model_turn", None)
                    has_output_transcription = bool(output_text)
                    if model_turn:
                        for part in getattr(model_turn, "parts", []) or []:
                            part_text = str(getattr(part, "text", "") or "")
                            if (
                                part_text
                                and not has_output_transcription
                                and not bool(getattr(part, "thought", False))
                            ):
                                self._assistant_buffer = self._merge_transcript_text(
                                    self._assistant_buffer,
                                    part_text,
                                )
                                self.transcript_received.emit("assistant", self._assistant_buffer, False)

                            inline_data = getattr(part, "inline_data", None)
                            data = getattr(inline_data, "data", None) if inline_data is not None else None
                            mime_type = str(getattr(inline_data, "mime_type", "") or "").lower()
                            if data and self._speaker_queue is not None and (
                                not mime_type or mime_type.startswith("audio/")
                            ):
                                self._audio_output_suppressed_until = time.monotonic() + 0.25
                                self.assistant_audio_level_changed.emit(self._compute_audio_level(data))
                                sample_rate = self._extract_audio_rate(mime_type)
                                await self._enqueue_speaker_audio(data, sample_rate)

                    if getattr(server_content, "interrupted", False):
                        self.session_state_changed.emit("interrupted")
                        self._assistant_buffer = ""
                        self._user_buffer = ""
                        self.assistant_audio_level_changed.emit(0.0)
                        self._clear_speaker_queue()

                    if getattr(server_content, "turn_complete", False):
                        if self._user_buffer:
                            self.transcript_received.emit("user", self._user_buffer, True)
                            self._user_buffer = ""
                        if self._assistant_buffer:
                            self.transcript_received.emit("assistant", self._assistant_buffer, True)
                            self._assistant_buffer = ""
                        self.assistant_audio_level_changed.emit(0.0)
                        self.session_state_changed.emit("listening")

                if not received_messages:
                    break
        except asyncio.CancelledError:
            raise
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
            self.error_received.emit(f"Live session error: {exc}")
            if self.enabled and not self._shutdown_event.is_set():
                await self._reconnect_with_resume()

    async def _handle_tool_call(self, tool_call: Any) -> None:
        responses = []
        function_calls = getattr(tool_call, "function_calls", None) or []
        for function_call in function_calls:
            args = self._parse_args(getattr(function_call, "args", None))
            if not args:
                args = self._parse_args(getattr(function_call, "arguments", None))
            result = await asyncio.to_thread(self.tools.execute, getattr(function_call, "name", ""), args)
            responses.append(
                types.FunctionResponse(
                    id=getattr(function_call, "id", None),
                    name=getattr(function_call, "name", ""),
                    response={"result": result},
                )
            )

        if responses and self._session is not None:
            await self._session.send_tool_response(function_responses=responses)

        while self._pending_capture_paths and self._session is not None:
            path, summary = self._pending_capture_paths.popleft()
            await self._send_capture_context(path, summary)

    async def _video_loop(self) -> None:
        assert self._session is not None
        if not self._video_stream_enabled:
            return
        interval = max(0.5, 1.0 / max(1, Config.LIVE_VIDEO_FPS))
        try:
            while True:
                frame = await asyncio.to_thread(self._capture_video_frame)
                if frame is not None:
                    blob = types.Blob(data=frame, mime_type="image/jpeg")
                    await self._session.send_realtime_input(video=blob)
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
        assert self._session is not None
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
        if self._session is None or not data:
            return
        blob = types.Blob(data=data, mime_type=f"audio/pcm;rate={Config.LIVE_AUDIO_INPUT_RATE}")
        await self._session.send_realtime_input(audio=blob)

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
                if not self.enabled or self._session is None:
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
        try:
            await self._disconnect_session()
            if self.enabled and not self._shutdown_event.is_set():
                await self._ensure_session()
        finally:
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
        poll_interval_s = 1.0
        nudge_cooldown_s = 2.5
        try:
            while True:
                await asyncio.sleep(poll_interval_s)

                if self._shutdown_event.is_set() or not self.enabled or self._session is None:
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
        payload = {
            "goal": self._current_goal,
            "workspace": self._workspace,
            "recent_user_steering": list(self._recent_user_steering),
            "recent_action_updates": list(self._recent_action_updates),
            "last_uia_summary": self.tools.last_snapshot_summary,
            "last_capture_summary": self.tools.last_capture_summary,
        }
        clean = {key: value for key, value in payload.items() if value}
        return json.dumps(clean, ensure_ascii=True)

    def _on_action_update(self, payload: dict[str, Any]) -> None:
        self._recent_action_updates.append(payload)
        status = str(payload.get("status") or "")
        if status == "queued":
            self.session_state_changed.emit("waiting")
        elif status == "running":
            self.session_state_changed.emit("acting")
        elif status == "cancel_requested":
            self.session_state_changed.emit("interrupted")
        elif self.enabled:
            self.session_state_changed.emit("listening")
        self.action_state_changed.emit(payload)

    def _on_capture_ready(self, screenshot_path: str, summary: dict[str, Any]) -> None:
        self._pending_capture_paths.append((screenshot_path, summary))

    async def _send_capture_context(self, screenshot_path: str, summary: dict[str, Any]) -> None:
        if self._session is None:
            return
        try:
            if self._image_input_enabled:
                with Image.open(screenshot_path) as image:
                    frame = self._image_to_bytes(image, max_size=(1280, 720), fmt="PNG")
                blob = types.Blob(data=frame, mime_type="image/png")
                await self._session.send_realtime_input(video=blob)
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

        if not queue.full():
            await queue.put(item)
            return

        try:
            await asyncio.wait_for(queue.put(item), timeout=0.20)
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
            if now - self._speaker_drop_logged_at > 2.0:
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
        if now - self._audio_resample_logged_at < 5.0:
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
