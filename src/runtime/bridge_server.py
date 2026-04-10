from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection

from .protocol import make_envelope, parse_envelope_text


logger = logging.getLogger("pixelpilot.runtime.bridge")


@dataclass
class _PendingRequest:
    event: threading.Event
    response: dict[str, Any] | None = None


class ElectronBridgeServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str = "",
    ) -> None:
        self.host = str(host or "127.0.0.1")
        self._port = int(port or 0)
        self.token = str(token or "").strip()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._ready_event = threading.Event()
        self._runtime_ready = False
        self._startup_error: BaseException | None = None

        self._lock = threading.RLock()
        self._control_connections: set[ServerConnection] = set()
        self._sidecar_connections: set[ServerConnection] = set()
        self._pending_requests: dict[str, _PendingRequest] = {}

        self._snapshot_provider: Callable[[], dict[str, Any]] | None = None
        self._command_handler: Callable[[str, dict[str, Any]], Any] | None = None

    @property
    def port(self) -> int:
        return int(self._port)

    def set_snapshot_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        self._snapshot_provider = provider

    def set_command_handler(self, handler: Callable[[str, dict[str, Any]], Any]) -> None:
        self._command_handler = handler

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._ready_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="PixelPilotElectronBridge", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=10.0):
            if self._startup_error is not None:
                raise RuntimeError("Electron bridge failed to start.") from self._startup_error
            if self._thread and not self._thread.is_alive():
                raise RuntimeError("Electron bridge thread exited before startup completed.")
            raise RuntimeError("Electron bridge failed to start in time.")

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and self._shutdown_event is not None:
            loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def has_control_clients(self) -> bool:
        with self._lock:
            return bool(self._control_connections)

    def has_sidecar_clients(self) -> bool:
        with self._lock:
            return bool(self._sidecar_connections)

    def set_runtime_ready(self, ready: bool = True) -> None:
        self._runtime_ready = bool(ready)
        self.publish_event("runtime.ready", {"ready": self._runtime_ready})

    def publish_event(self, method: str, payload: dict[str, Any] | None = None) -> None:
        self._dispatch_json_to_controls(make_envelope("event", method, payload or {}))

    def publish_state_snapshot(self) -> None:
        if self._snapshot_provider is None:
            return
        self.publish_event("state.snapshot", self._snapshot_provider())

    def publish_state_updated(self) -> None:
        if self._snapshot_provider is None:
            return
        self.publish_event("state.updated", self._snapshot_provider())

    def publish_sidecar_frame(self, packet: bytes) -> None:
        if not packet:
            return
        loop = self._loop
        if loop is None or not self.has_sidecar_clients():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_sidecar(bytes(packet)), loop)

    def request_ui(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_s: float = 30.0,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        if not self.has_control_clients():
            return {} if allow_missing else {"error": "ui_unavailable"}

        request = make_envelope("request", method, payload or {})
        pending = _PendingRequest(event=threading.Event())
        with self._lock:
            self._pending_requests[request["id"]] = pending

        self._dispatch_json_to_controls(request)
        finished = pending.event.wait(timeout=max(0.5, float(timeout_s or 30.0)))

        with self._lock:
            self._pending_requests.pop(request["id"], None)

        if not finished:
            if allow_missing:
                return {}
            raise TimeoutError(f"Timed out waiting for UI response to {method}.")
        return dict(pending.response or {})

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._shutdown_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001
            self._startup_error = exc
            logger.exception("Electron bridge server crashed during startup.")
            self._ready_event.set()
            raise
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _serve(self) -> None:
        async with websockets.serve(
            self._handle_connection,
            self.host,
            self._port,
            max_size=20_000_000,
        ) as server:
            sockets = list(getattr(server, "sockets", []) or [])
            if sockets:
                self._port = int(sockets[0].getsockname()[1])
            logger.info("Electron bridge listening on ws://%s:%d", self.host, self._port)
            self._ready_event.set()
            await self._shutdown_event.wait()

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        parsed = self._connection_route(websocket)
        token = parsed["token"]
        path = parsed["path"]

        if self.token and token != self.token:
            await websocket.close(code=4001, reason="Unauthorized")
            return

        if path == "/sidecar":
            await self._handle_sidecar_connection(websocket)
            return

        await self._handle_control_connection(websocket)

    def _connection_route(self, websocket: ServerConnection) -> dict[str, str]:
        request = getattr(websocket, "request", None)
        raw_path = str(getattr(request, "path", "/control") or "/control")
        parsed = urlsplit(raw_path)
        token = parse_qs(parsed.query).get("token", [""])[0]
        return {
            "path": parsed.path or "/control",
            "token": str(token or ""),
        }

    async def _handle_control_connection(self, websocket: ServerConnection) -> None:
        with self._lock:
            self._control_connections.add(websocket)

        try:
            await self._send_json(websocket, make_envelope("event", "runtime.ready", {"ready": self._runtime_ready}))
            if self._snapshot_provider is not None:
                await self._send_json(websocket, make_envelope("event", "state.snapshot", self._snapshot_provider()))

            async for message in websocket:
                if isinstance(message, bytes):
                    continue
                await self._handle_control_message(websocket, str(message))
        finally:
            with self._lock:
                self._control_connections.discard(websocket)

    async def _handle_sidecar_connection(self, websocket: ServerConnection) -> None:
        with self._lock:
            self._sidecar_connections.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            with self._lock:
                self._sidecar_connections.discard(websocket)

    async def _handle_control_message(self, websocket: ServerConnection, raw_text: str) -> None:
        try:
            envelope = parse_envelope_text(raw_text)
        except ValueError as exc:
            await self._send_json(
                websocket,
                make_envelope(
                    "error",
                    "runtime.error",
                    {"message": str(exc)},
                ),
            )
            return

        kind = envelope["kind"]
        if kind == "response":
            self._resolve_pending_request(envelope["id"], envelope["payload"])
            return

        if kind != "command":
            return

        try:
            payload = await self._call_command_handler(envelope["method"], envelope["payload"])
            response = make_envelope(
                "response",
                envelope["method"],
                payload or {},
                message_id=envelope["id"],
            )
            await self._send_json(websocket, response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Bridge command failed: %s", envelope["method"])
            error_payload = {
                "message": str(exc),
                "method": envelope["method"],
            }
            await self._send_json(
                websocket,
                make_envelope("error", "runtime.error", error_payload, message_id=envelope["id"]),
            )

    async def _call_command_handler(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._command_handler is None:
            return {}
        result = self._command_handler(str(method or ""), dict(payload or {}))
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return {}
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def _resolve_pending_request(self, request_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending_requests.get(str(request_id or ""))
        if pending is None:
            return
        pending.response = dict(payload or {})
        pending.event.set()

    def _dispatch_json_to_controls(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_json(payload), loop)

    async def _broadcast_json(self, payload: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._control_connections)
        for websocket in targets:
            try:
                await self._send_json(websocket, payload)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to send control payload", exc_info=True)

    async def _broadcast_sidecar(self, packet: bytes) -> None:
        with self._lock:
            targets = list(self._sidecar_connections)
        for websocket in targets:
            try:
                await websocket.send(packet)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to send sidecar frame", exc_info=True)

    @staticmethod
    async def _send_json(websocket: ServerConnection, payload: dict[str, Any]) -> None:
        await websocket.send(json.dumps(payload))
