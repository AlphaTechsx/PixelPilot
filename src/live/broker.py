from __future__ import annotations

import logging
import queue
import threading
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
    ) -> None:
        self._on_action_update = on_action_update
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[ActionRecord, Callable[..., dict[str, Any]]]] = queue.Queue()
        self._actions: dict[str, ActionRecord] = {}
        self._pending_record: Optional[ActionRecord] = None
        self._current_record: Optional[ActionRecord] = None
        self._current_cancel_event: Optional[threading.Event] = None
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, name="LiveActionBroker", daemon=True)
        self._worker.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self.cancel_current_action("Broker shutting down.")
        self._worker.join(timeout=2.0)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending_record or self._current_record)

    def current_action_payload(self) -> Optional[dict[str, Any]]:
        with self._lock:
            record = self._current_record or self._pending_record
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
            if self._pending_record or self._current_record:
                record = self._new_record(
                    name=name,
                    args=payload_args,
                    status="failed",
                    message="Another action is already in progress.",
                    error="busy",
                    finished=True,
                )
                self._actions[record.action_id] = record
                self._emit(record)
                return record.to_payload()

            record = self._new_record(
                name=name,
                args=payload_args,
                status="queued",
                message=f"{name} queued",
            )
            self._actions[record.action_id] = record
            self._pending_record = record
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

    def cancel_current_action(self, message: str = "Stop requested.") -> Optional[dict[str, Any]]:
        with self._lock:
            if self._current_record and self._current_record.status in {"running", "cancel_requested"}:
                self._current_record.mark("cancel_requested", message=message)
                if self._current_cancel_event is not None:
                    self._current_cancel_event.set()
                self._emit(self._current_record)
                return self._current_record.to_payload()

            if self._pending_record and self._pending_record.status == "queued":
                record = self._pending_record
                record.mark("cancelled", message=message, error="cancelled", finished=True)
                self._emit(record)
                self._pending_record = None
                return record.to_payload()

        return None

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

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                record, handler = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._lock:
                if self._pending_record and self._pending_record.action_id == record.action_id:
                    self._pending_record = None
                self._current_record = record
                self._current_cancel_event = threading.Event()
                cancel_event = self._current_cancel_event

            if cancel_event.is_set() or record.status in {"cancel_requested", "cancelled"}:
                record.mark("cancelled", message="Action cancelled before execution.", error="cancelled", finished=True)
                self._emit(record)
                self._clear_current(record.action_id)
                continue

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
                else:
                    record.mark(
                        "failed",
                        message=message or f"{record.name} failed",
                        result=result,
                        error=str(result.get("error") or "failed") if isinstance(result, dict) else "failed",
                        finished=True,
                    )
            except ActionCancelledError as exc:
                record.mark("cancelled", message=str(exc) or "Action cancelled.", error="cancelled", finished=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Live action failed: %s", record.name)
                record.mark("failed", message=f"{record.name} failed", error=str(exc), finished=True)

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
            "status": "failed",
            "message": "Unknown action.",
            "result": None,
            "error": "unknown_action",
            "started_at": None,
            "updated_at": None,
            "finished_at": None,
        }
