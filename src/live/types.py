from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event
from typing import Any, Literal, Optional


ActionStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionCancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class ActionRecord:
    action_id: str
    name: str
    args: dict[str, Any]
    status: ActionStatus
    message: str
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    done_event: Event = field(default_factory=Event, repr=False)

    def mark(
        self,
        status: ActionStatus,
        *,
        message: Optional[str] = None,
        result: Any = None,
        error: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        self.status = status
        if message is not None:
            self.message = str(message)
        self.result = result
        self.error = error
        now = utc_now_iso()
        self.updated_at = now
        if status == "running" and not self.started_at:
            self.started_at = now
        if finished or status in {"succeeded", "failed", "cancelled"}:
            self.finished_at = now
            self.done_event.set()

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }
