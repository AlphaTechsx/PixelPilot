from __future__ import annotations

import asyncio
import base64
import copy
import logging
import os
import uuid
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import WebSocket
from google import genai
from google.genai import types

import rate_limiter

load_dotenv()

logger = logging.getLogger("backend.live")

LIVE_API_KEY = os.getenv("GEMINI_API_KEY")
DEFAULT_LIVE_MODEL = (
    os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview").strip()
    or "gemini-3.1-flash-live-preview"
)
LIVE_PROVIDER_ERROR_MESSAGE = "Gemini Live session failed"


class LiveSessionError(RuntimeError):
    def __init__(self, code: int, detail: Any):
        super().__init__(str(detail))
        self.code = int(code)
        self.detail = detail


def _build_provider_rate_limit_detail(exc: Exception) -> dict[str, Any]:
    message = str(exc or "").strip() or "Gemini Live rate limit exceeded."
    return {
        "message": message,
        "window": "provider",
        "limit": None,
        "remaining": None,
        "retry_after_seconds": None,
        "scope": "live_provider",
    }


def _normalize_function_call_args(raw_args: Any) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            import json

            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_live_model(raw_model: Any) -> str:
    model = str(raw_model or "").strip()
    return model or DEFAULT_LIVE_MODEL


def _normalize_provider_response(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    session_resumption_update = getattr(response, "session_resumption_update", None)
    if session_resumption_update:
        handle = getattr(session_resumption_update, "new_handle", None) or getattr(
            session_resumption_update, "resumption_handle", None
        )
        if handle:
            payload["session_resumption_update"] = {
                "handle": str(handle),
                "resumable": bool(getattr(session_resumption_update, "resumable", False)),
            }

    tool_call = getattr(response, "tool_call", None)
    function_calls = []
    if tool_call:
        for function_call in getattr(tool_call, "function_calls", None) or []:
            function_calls.append(
                {
                    "id": getattr(function_call, "id", None),
                    "name": str(getattr(function_call, "name", "") or ""),
                    "args": _normalize_function_call_args(
                        getattr(function_call, "args", None)
                    )
                    or _normalize_function_call_args(
                        getattr(function_call, "arguments", None)
                    ),
                }
            )
    if function_calls:
        payload["tool_call"] = {"function_calls": function_calls}

    server_content = getattr(response, "server_content", None)
    if server_content:
        server_payload: dict[str, Any] = {}

        input_transcription = getattr(server_content, "input_transcription", None)
        if input_transcription:
            text = str(getattr(input_transcription, "text", "") or "")
            if text:
                server_payload["input_transcription"] = {"text": text}

        output_transcription = getattr(server_content, "output_transcription", None)
        if output_transcription:
            text = str(getattr(output_transcription, "text", "") or "")
            if text:
                server_payload["output_transcription"] = {"text": text}

        model_turn = getattr(server_content, "model_turn", None)
        parts_payload = []
        if model_turn:
            for part in getattr(model_turn, "parts", None) or []:
                item: dict[str, Any] = {}
                text = str(getattr(part, "text", "") or "")
                if text:
                    item["text"] = text
                if bool(getattr(part, "thought", False)):
                    item["thought"] = True
                inline_data = getattr(part, "inline_data", None)
                data = getattr(inline_data, "data", None) if inline_data is not None else None
                mime_type = (
                    str(getattr(inline_data, "mime_type", "") or "")
                    if inline_data is not None
                    else ""
                )
                if data is not None:
                    item["inline_data"] = {
                        "data": base64.b64encode(bytes(data)).decode("ascii"),
                        "mime_type": mime_type,
                    }
                if item:
                    parts_payload.append(item)
        if parts_payload:
            server_payload["model_turn"] = {"parts": parts_payload}

        if bool(getattr(server_content, "interrupted", False)):
            server_payload["interrupted"] = True
        if bool(getattr(server_content, "generation_complete", False)):
            server_payload["generation_complete"] = True
        if bool(getattr(server_content, "turn_complete", False)):
            server_payload["turn_complete"] = True

        if server_payload:
            payload["server_content"] = server_payload

    go_away = getattr(response, "go_away", None)
    if go_away:
        message = str(getattr(go_away, "message", "") or "")
        time_left = getattr(go_away, "time_left", None)
        go_away_payload: dict[str, Any] = {}
        if message:
            go_away_payload["message"] = message
        if time_left is not None:
            go_away_payload["time_left"] = str(time_left)
        if go_away_payload:
            payload["go_away"] = go_away_payload

    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is not None:
        usage_payload: dict[str, Any] = {}
        total_token_count = getattr(usage_metadata, "total_token_count", None)
        if total_token_count is not None:
            usage_payload["total_token_count"] = int(total_token_count)
        if usage_payload:
            payload["usage_metadata"] = usage_payload

    return payload


class BackendLiveSession:
    def __init__(
        self,
        *,
        websocket: WebSocket,
        user_id: str,
        redis_client,
    ) -> None:
        if not LIVE_API_KEY:
            raise LiveSessionError(503, "Gemini Live backend is not configured.")

        self.websocket = websocket
        self.user_id = str(user_id or "").strip()
        self.redis_client = redis_client
        self._send_lock = asyncio.Lock()
        self._client: Optional[genai.Client] = None
        self._session = None
        self._session_cm = None
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reservation: Optional[rate_limiter.LiveSessionReservation] = None
        self._resume_handle: Optional[str] = None
        self._connect_config: dict[str, Any] = {}
        self._model = DEFAULT_LIVE_MODEL
        self._closed = False

    @property
    def started(self) -> bool:
        return self._session is not None

    async def start(
        self,
        connect_config: Optional[dict[str, Any]],
        *,
        model: Optional[str] = None,
    ) -> None:
        if self._session is not None:
            raise LiveSessionError(400, "Live session already started.")
        if self.redis_client is None:
            raise LiveSessionError(503, "Service temporarily unavailable")

        session_id = uuid.uuid4().hex
        reservation = await rate_limiter.reserve_live_session_start(
            self.user_id,
            session_id,
            self.redis_client,
        )
        if not reservation.allowed:
            raise LiveSessionError(429, self._build_rate_limit_detail(reservation))

        self._reservation = reservation
        self._connect_config = copy.deepcopy(connect_config or {})
        self._model = _normalize_live_model(model)
        try:
            await self._ensure_session_with_retry()
        except Exception as exc:  # noqa: BLE001
            await rate_limiter.refund_live_session_start(reservation, self.redis_client)
            self._reservation = None
            raise self._translate_provider_exception(exc)

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._receive_task = asyncio.create_task(self._receive_loop())
        await self._send_json({"type": "live_started", "model": self._model})

    async def send_text(self, text: str) -> None:
        payload = str(text or "")

        async def _sender() -> None:
            await self._session.send_realtime_input(text=payload)

        await self._send_with_retry(_sender)

    async def send_audio(self, data: bytes, mime_type: str) -> None:
        blob = types.Blob(data=data, mime_type=mime_type)

        async def _sender() -> None:
            await self._session.send_realtime_input(audio=blob)

        await self._send_with_retry(_sender)

    async def send_video(self, data: bytes, mime_type: str) -> None:
        blob = types.Blob(data=data, mime_type=mime_type)

        async def _sender() -> None:
            await self._session.send_realtime_input(video=blob)

        await self._send_with_retry(_sender)

    async def send_audio_stream_end(self) -> None:
        async def _sender() -> None:
            await self._session.send_realtime_input(audio_stream_end=True)

        await self._send_with_retry(_sender)

    async def send_tool_responses(self, responses: list[dict[str, Any]]) -> None:
        provider_responses = []
        for item in responses or []:
            if not isinstance(item, dict):
                continue
            provider_responses.append(
                types.FunctionResponse(
                    id=item.get("id"),
                    name=str(item.get("name") or ""),
                    response=dict(item.get("response") or {}),
                )
            )
        if not provider_responses:
            return

        async def _sender() -> None:
            await self._session.send_tool_response(
                function_responses=provider_responses
            )

        await self._send_with_retry(_sender)

    async def stop(
        self,
        *,
        notify_client: bool = False,
        close_client: bool = False,
    ) -> None:
        current_task = asyncio.current_task()
        tasks = [self._receive_task, self._heartbeat_task]
        pending: list[asyncio.Task] = []
        for task in tasks:
            if task is None or task.done() or task is current_task:
                continue
            task.cancel()
            pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        self._receive_task = None
        self._heartbeat_task = None

        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("Failed to close backend live session", exc_info=True)
        self._session_cm = None
        self._session = None

        if self._reservation is not None and self.redis_client is not None:
            try:
                await rate_limiter.release_live_session(
                    self._reservation,
                    self.redis_client,
                )
            except Exception:
                logger.debug("Failed to release live reservation", exc_info=True)
        self._reservation = None

        if notify_client:
            await self._send_json({"type": "live_closed"})

        if close_client and self._client is not None:
            try:
                await self._client.aio.aclose()
            except Exception:
                logger.debug("Failed to close backend Gemini client", exc_info=True)
            self._client = None

    async def shutdown(self) -> None:
        self._closed = True
        await self.stop(close_client=True)

    async def _send_with_retry(self, sender, *, allow_retry: bool = True) -> None:
        await self._ensure_session_with_retry()
        try:
            await sender()
        except Exception as exc:  # noqa: BLE001
            if (
                allow_retry
                and not self._closed
                and self._is_recoverable_connection_error(exc)
            ):
                logger.warning("Backend live send failed; reconnecting: %s", exc)
                await self._reconnect_with_resume()
                await self._send_with_retry(sender, allow_retry=False)
                return
            raise self._translate_provider_exception(exc)

    async def _ensure_session_with_retry(self, retries: int = 1):
        attempt = 0
        retried_without_resumption = False
        while True:
            try:
                return await self._ensure_session()
            except Exception as exc:  # noqa: BLE001
                if self._is_nonrecoverable_request_error(exc):
                    had_resume_handle = bool(str(self._resume_handle or "").strip())
                    if had_resume_handle and not retried_without_resumption:
                        retried_without_resumption = True
                        self._clear_resume_handle(reason=f"connect_rejected: {exc}")
                        logger.warning(
                            "Backend live connect rejected resumption handle; retrying once without resumption."
                        )
                        await self._disconnect_provider()
                        await asyncio.sleep(0.05)
                        continue
                    raise
                if attempt >= retries or not self._is_recoverable_connection_error(exc):
                    raise
                delay_s = 0.75 * (attempt + 1)
                logger.warning(
                    "Backend live connect failed (%s); retrying in %.2fs",
                    exc,
                    delay_s,
                )
                await self._disconnect_provider()
                await asyncio.sleep(delay_s)
                attempt += 1

    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        if self._client is None:
            self._client = genai.Client(api_key=LIVE_API_KEY)
        config = self._build_connect_config()
        self._session_cm = self._client.aio.live.connect(model=self._model, config=config)
        self._session = await self._session_cm.__aenter__()
        return self._session

    async def _disconnect_provider(self) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("Failed to disconnect backend live provider", exc_info=True)
        self._session_cm = None
        self._session = None

    def _clear_resume_handle(self, *, reason: str = "") -> None:
        handle = str(self._resume_handle or "").strip()
        if not handle:
            return
        logger.warning(
            "Clearing backend Gemini Live session resumption handle%s.",
            f" ({reason})" if str(reason or "").strip() else "",
        )
        self._resume_handle = None

    def _build_connect_config(self) -> dict[str, Any]:
        config = copy.deepcopy(self._connect_config)
        config.pop("model", None)
        if self._resume_handle:
            config["session_resumption"] = {"handle": self._resume_handle}
        return config

    async def _heartbeat_loop(self) -> None:
        while not self._closed and self._reservation is not None:
            await asyncio.sleep(rate_limiter.LIVE_SESSION_HEARTBEAT_SECONDS)
            if self._reservation is None or self.redis_client is None:
                return
            try:
                await rate_limiter.refresh_live_session_lease(
                    self._reservation,
                    self.redis_client,
                )
            except Exception:
                logger.debug("Failed to refresh live lease heartbeat", exc_info=True)

    async def _receive_loop(self) -> None:
        while not self._closed and self._session is not None:
            try:
                received_messages = False
                async for response in self._session.receive():
                    received_messages = True
                    if self._closed:
                        break
                    event = _normalize_provider_response(response)
                    update = event.get("session_resumption_update")
                    if isinstance(update, dict) and update.get("handle"):
                        self._resume_handle = str(update.get("handle"))
                    if event:
                        await self._send_json({"type": "live_event", "event": event})

                if self._closed:
                    return
                if received_messages:
                    logger.debug(
                        "Backend live receive stream ended after events; re-subscribing to provider stream."
                    )
                    await asyncio.sleep(0.05)
                    continue

                logger.warning(
                    "Backend live receive stream ended before any messages; reconnecting with resumption."
                )
                try:
                    await self._reconnect_with_resume()
                    continue
                except Exception as reconnect_exc:  # noqa: BLE001
                    await self._send_error(self._translate_provider_exception(reconnect_exc))
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not self._closed and self._is_recoverable_connection_error(exc):
                    logger.warning("Backend live receive lost connection; reconnecting: %s", exc)
                    try:
                        await self._reconnect_with_resume()
                        continue
                    except Exception as reconnect_exc:  # noqa: BLE001
                        await self._send_error(self._translate_provider_exception(reconnect_exc))
                        break
                elif not self._closed and self._is_nonrecoverable_request_error(exc):
                    self._clear_resume_handle(reason=f"receive_error: {exc}")
                    await self._send_error(
                        LiveSessionError(
                            400,
                            "Gemini Live rejected the request (invalid argument). "
                            "Session disconnected to prevent reconnect loops.",
                        )
                    )
                    break
                elif not self._closed:
                    await self._send_error(self._translate_provider_exception(exc))
                    break
                else:
                    break
        if not self._closed:
            await self.stop(notify_client=True)

    async def _reconnect_with_resume(self) -> None:
        await self._disconnect_provider()
        if not self._closed:
            await self._ensure_session_with_retry()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(payload)

    async def _send_error(self, exc: LiveSessionError) -> None:
        await self._send_json({"type": "error", "code": exc.code, "detail": exc.detail})

    def _translate_provider_exception(self, exc: Exception) -> LiveSessionError:
        message = str(exc or "").lower()
        if "429" in message or "rate" in message or "quota" in message:
            return LiveSessionError(429, _build_provider_rate_limit_detail(exc))
        return LiveSessionError(500, LIVE_PROVIDER_ERROR_MESSAGE)

    @staticmethod
    def _build_rate_limit_detail(
        reservation: rate_limiter.LiveSessionReservation,
    ) -> dict[str, Any]:
        if reservation.window == rate_limiter.WINDOW_LIVE_CONCURRENT:
            message = "Another Gemini Live session is already active. Try again shortly."
        else:
            message = (
                f"Gemini Live daily time limit exceeded ({reservation.limit // 60} minutes). "
                f"Resets at midnight UTC."
            )
        return {
            "message": message,
            "window": reservation.window,
            "limit": reservation.limit,
            "remaining": reservation.remaining,
            "retry_after_seconds": reservation.retry_after_seconds,
            "scope": reservation.scope,
        }

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
