from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Optional

from .types import ActionCancelledError, ActionRecord

logger = logging.getLogger("pixelpilot.live.broker")


class LiveActionBroker:
    """
    Serialize side-effectful Live actions so the model can observe explicit
    queued/running/completed state and avoid overlapping desktop actions.
    """

    def __init__(
        self,
        *,
        on_action_update: Optional[Callable[[dict[str, Any]], None]] = None,
        wait_gate: Optional[Callable[[], Any]] = None,
        on_waiting: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_action_update = on_action_update
        self._wait_gate = wait_gate
        self._on_waiting = on_waiting
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[ActionRecord, Callable[..., dict[str, Any]]]] = queue.Queue()
        self._actions: dict[str, ActionRecord] = {}
        self._queued_action_ids: list[str] = []
        self._current_record: Optional[ActionRecord] = None
        self._current_cancel_event: Optional[threading.Event] = None
        self._stop_event = threading.Event()
        self._last_wait_message = ""
        self._last_wait_message_at = 0.0
        self._worker = threading.Thread(target=self._worker_loop, name="LiveActionBroker", daemon=True)
        self._worker.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self.cancel_current_action("Broker shutting down.")
        self._worker.join(timeout=2.0)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._queued_action_ids or self._current_record)

    def current_action_payload(self) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self._current_record
            if record is None:
                while self._queued_action_ids:
                    action_id = self._queued_action_ids[0]
                    queued = self._actions.get(action_id)
                    if queued and queued.status == "queued":
                        record = queued
                        break
                    self._queued_action_ids.pop(0)
            return record.to_payload() if record else None

    def submit(
        self,
        *,
        name: str,
        args: Optional[dict[str, Any]],
        handler: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        payload_args = dict(args or {})
        with self._lock:
            record = self._new_record(
                name=name,
                args=payload_args,
                status="queued",
                message=f"{name} queued",
            )
            self._actions[record.action_id] = record
            self._queued_action_ids.append(record.action_id)
            self._queue.put((record, handler))
            self._emit(record)
            return record.to_payload()

    def get_action_status(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._actions.get(str(action_id or "").strip())
            if record:
                return record.to_payload()
        return self._unknown_action_payload(action_id)

    def wait_for_action(self, action_id: str, timeout_ms: int = 1000) -> dict[str, Any]:
        with self._lock:
            record = self._actions.get(str(action_id or "").strip())
        if not record:
            return self._unknown_action_payload(action_id)

        timeout_s = max(0.0, float(timeout_ms or 0) / 1000.0)
        record.done_event.wait(timeout_s)
        return record.to_payload()

    def update_current_action(
        self,
        message: str,
        *,
        result: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        clean_message = str(message or "").strip()
        with self._lock:
            record = self._current_record
            if record is None:
                return None
            if record.status not in {"running", "cancel_requested"}:
                return None
            if clean_message:
                record.mark(
                    "running",
                    message=clean_message,
                    result=result if result is not None else record.result,
                )
            elif result is not None:
                record.mark("running", result=result)
            payload = record.to_payload()

        self._emit(record)
        return payload

    def cancel_current_action(self, message: str = "Stop requested.") -> Optional[dict[str, Any]]:
        cancelled_payload: Optional[dict[str, Any]] = None
        with self._lock:
            if self._current_record and self._current_record.status in {"running", "cancel_requested"}:
                self._current_record.mark("cancel_requested", message=message)
                if self._current_cancel_event is not None:
                    self._current_cancel_event.set()
                self._emit(self._current_record)
                cancelled_payload = self._current_record.to_payload()

            queued_ids = list(self._queued_action_ids)
            self._queued_action_ids.clear()

            for action_id in queued_ids:
                record = self._actions.get(action_id)
                if not record or record.status != "queued":
                    continue
                record.mark("cancelled", message=message, error="cancelled", finished=True)
                self._emit(record)
                if cancelled_payload is None:
                    cancelled_payload = record.to_payload()

        return cancelled_payload

    def _new_record(
        self,
        *,
        name: str,
        args: dict[str, Any],
        status: str,
        message: str,
        error: Optional[str] = None,
        finished: bool = False,
    ) -> ActionRecord:
        record = ActionRecord(
            action_id=str(uuid.uuid4()),
            name=name,
            args=args,
            status=status,  # type: ignore[arg-type]
            message=message,
            error=error,
        )
        if finished:
            record.finished_at = record.updated_at
            record.done_event.set()
        return record

    def _emit(self, record: ActionRecord) -> None:
        if not self._on_action_update:
            return
        try:
            self._on_action_update(record.to_payload())
        except Exception:
            logger.debug("Failed to emit live action update", exc_info=True)

    def _notify_waiting(self, message: str) -> None:
        callback = self._on_waiting
        if callback is None:
            return

        clean_message = str(message or "").strip()
        if not clean_message:
            return

        now = time.monotonic()
        should_emit = (
            clean_message != self._last_wait_message
            or (now - self._last_wait_message_at) >= 1.0
        )
        if not should_emit:
            return

        self._last_wait_message = clean_message
        self._last_wait_message_at = now

        try:
            callback(clean_message)
        except Exception:
            logger.debug("Failed to emit broker waiting note", exc_info=True)

    def _gate_state(self) -> tuple[bool, str]:
        checker = self._wait_gate
        if checker is None:
            return False, ""

        try:
            raw = checker()
        except Exception:
            logger.debug("Live action wait gate failed", exc_info=True)
            return False, ""

        active = False
        message = ""
        if isinstance(raw, dict):
            active = bool(raw.get("active"))
            message = str(
                raw.get("message")
                or raw.get("reason")
                or ""
            ).strip()
        elif isinstance(raw, tuple) and raw:
            active = bool(raw[0])
            if len(raw) > 1:
                message = str(raw[1] or "").strip()
        else:
            active = bool(raw)

        if active and not message:
            message = "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."

        return active, message

    def _wait_until_unblocked(self, record: ActionRecord, cancel_event: threading.Event) -> bool:
        while not self._stop_event.is_set():
            if cancel_event.is_set() or record.status in {"cancel_requested", "cancelled"}:
                return False

            gate_active, gate_message = self._gate_state()
            if not gate_active:
                return True

            wait_message = gate_message or "UAC mode active. Waiting for orchestrator to resolve secure desktop prompt."
            record.mark("queued", message=wait_message)
            self._emit(record)
            self._notify_waiting(wait_message)
            time.sleep(0.15)

        return False

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                record, handler = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._lock:
                if record.action_id in self._queued_action_ids:
                    self._queued_action_ids.remove(record.action_id)
                self._current_record = record
                self._current_cancel_event = threading.Event()
                cancel_event = self._current_cancel_event

            if cancel_event.is_set() or record.status in {"cancel_requested", "cancelled"}:
                record.mark("cancelled", message="Action cancelled before execution.", error="cancelled", finished=True)
                self._emit(record)
                self._clear_current(record.action_id)
                continue

            if not self._wait_until_unblocked(record, cancel_event):
                record.mark("cancelled", message="Action cancelled while waiting.", error="cancelled", finished=True)
                self._emit(record)
                self._clear_current(record.action_id)
                continue

            while True:
                if cancel_event.is_set() or record.status in {"cancel_requested", "cancelled"}:
                    record.mark("cancelled", message="Action cancelled before execution.", error="cancelled", finished=True)
                    break

                if not self._wait_until_unblocked(record, cancel_event):
                    record.mark("cancelled", message="Action cancelled while waiting.", error="cancelled", finished=True)
                    break

                record.mark("running", message=f"{record.name} running")
                self._emit(record)

                try:
                    result = handler(cancel_event=cancel_event)
                    if cancel_event.is_set() and isinstance(result, dict) and result.get("cancelled"):
                        raise ActionCancelledError(str(result.get("message") or "Action cancelled."))

                    success = bool(isinstance(result, dict) and result.get("success", True))
                    message = ""
                    if isinstance(result, dict):
                        message = str(result.get("message") or "")

                    if success:
                        record.mark(
                            "succeeded",
                            message=message or f"{record.name} completed",
                            result=result,
                            finished=True,
                        )
                        break

                    gate_active, gate_message = self._gate_state()
                    if gate_active:
                        wait_message = gate_message or (
                            "UAC mode active. Action paused and will resume automatically once resolved."
                        )
                        record.mark("queued", message=wait_message, result=result)
                        self._emit(record)
                        self._notify_waiting(wait_message)
                        continue

                    record.mark(
                        "failed",
                        message=message or f"{record.name} failed",
                        result=result,
                        error=str(result.get("error") or "failed") if isinstance(result, dict) else "failed",
                        finished=True,
                    )
                    break
                except ActionCancelledError as exc:
                    record.mark("cancelled", message=str(exc) or "Action cancelled.", error="cancelled", finished=True)
                    break
                except Exception as exc:  # noqa: BLE001
                    gate_active, gate_message = self._gate_state()
                    if gate_active:
                        wait_message = gate_message or (
                            "UAC mode active. Action paused and will resume automatically once resolved."
                        )
                        record.mark("queued", message=wait_message, error="uac_waiting")
                        self._emit(record)
                        self._notify_waiting(wait_message)
                        continue

                    logger.exception("Live action failed: %s", record.name)
                    record.mark("failed", message=f"{record.name} failed", error=str(exc), finished=True)
                    break

            self._emit(record)
            self._clear_current(record.action_id)

    def _clear_current(self, action_id: str) -> None:
        with self._lock:
            if self._current_record and self._current_record.action_id == action_id:
                self._current_record = None
            self._current_cancel_event = None

    @staticmethod
    def _unknown_action_payload(action_id: str) -> dict[str, Any]:
        return {
            "action_id": str(action_id or ""),
            "name": "",
            "args": {},
            "status": "failed",
            "message": "Unknown action.",
            "result": None,
            "error": "unknown_action",
            "created_at": None,
            "started_at": None,
            "updated_at": None,
            "finished_at": None,
            "done": True,
        }
