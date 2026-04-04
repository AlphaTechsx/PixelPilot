from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
)

from config import Config, OperationMode


def _normalize_mode(value: Any) -> str:
    if isinstance(value, OperationMode):
        value = value.value
    key = str(value or Config.DEFAULT_MODE.value).strip().lower() or Config.DEFAULT_MODE.value
    if key == "guide":
        return "GUIDANCE"
    if key == "safe":
        return "SAFE"
    if key == "auto":
        return "AUTO"
    return "SAFE"


def _normalize_vision(value: Any) -> str:
    key = str(value or "OCR").strip().upper() or "OCR"
    if key not in {"OCR", "ROBO"}:
        key = "OCR"
    return key


def _normalize_workspace(value: Any) -> str:
    key = str(value or Config.DEFAULT_WORKSPACE).strip().lower() or Config.DEFAULT_WORKSPACE
    if key not in {"user", "agent"}:
        key = "user"
    return key


class UiStateStore(QObject):
    operationModeChanged = Signal()
    visionModeChanged = Signal()
    workspaceChanged = Signal()
    liveAvailabilityChanged = Signal()
    liveEnabledChanged = Signal()
    liveVoiceActiveChanged = Signal()
    liveSessionStateChanged = Signal()
    userAudioLevelChanged = Signal()
    assistantAudioLevelChanged = Signal()
    expandedChanged = Signal()
    backgroundHiddenChanged = Signal()
    agentViewEnabledChanged = Signal()
    agentViewRequestedChanged = Signal()
    agentViewVisibleChanged = Signal()
    clickThroughEnabledChanged = Signal()
    agentPreviewAvailableChanged = Signal()
    sidecarVisibleChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._operation_mode = _normalize_mode(Config.DEFAULT_MODE)
        self._vision_mode = "ROBO" if Config.USE_ROBOTICS_EYE else "OCR"
        self._workspace = _normalize_workspace(Config.DEFAULT_WORKSPACE)
        self._live_available = bool(Config.LIVE_MODE_AVAILABLE)
        self._live_unavailable_reason = ""
        self._live_enabled = bool(self._live_available and Config.LIVE_MODE_DEFAULT_ENABLED)
        self._live_voice_active = bool(self._live_enabled and Config.LIVE_MODE_DEFAULT_VOICE_ENABLED)
        self._live_session_state = "disconnected"
        self._user_audio_level = 0.0
        self._assistant_audio_level = 0.0
        self._expanded = False
        self._background_hidden = False
        self._agent_view_enabled = self._workspace == "agent"
        self._agent_view_requested = self._agent_view_enabled
        self._click_through_enabled = False
        self._agent_preview_available = False
        self._sidecar_visible = False

    @Property(str, notify=operationModeChanged)
    def operationMode(self) -> str:
        return self._operation_mode

    def set_operation_mode(self, value: Any) -> None:
        normalized = _normalize_mode(value)
        if normalized == self._operation_mode:
            return
        self._operation_mode = normalized
        self.operationModeChanged.emit()

    @Property(str, notify=visionModeChanged)
    def visionMode(self) -> str:
        return self._vision_mode

    def set_vision_mode(self, value: Any) -> None:
        normalized = _normalize_vision(value)
        if normalized == self._vision_mode:
            return
        self._vision_mode = normalized
        self.visionModeChanged.emit()

    @Property(str, notify=workspaceChanged)
    def workspace(self) -> str:
        return self._workspace

    def set_workspace(self, value: Any) -> None:
        normalized = _normalize_workspace(value)
        if normalized == self._workspace:
            return
        self._workspace = normalized
        self.workspaceChanged.emit()

    @Property(bool, notify=liveAvailabilityChanged)
    def liveAvailable(self) -> bool:
        return self._live_available

    @Property(str, notify=liveAvailabilityChanged)
    def liveUnavailableReason(self) -> str:
        return self._live_unavailable_reason

    def set_live_availability(self, available: bool, reason: str = "") -> None:
        changed = False
        available = bool(available)
        reason = str(reason or "").strip()
        if available != self._live_available:
            self._live_available = available
            changed = True
        if reason != self._live_unavailable_reason:
            self._live_unavailable_reason = reason
            changed = True
        if not available and self._live_enabled:
            self._live_enabled = False
            self.liveEnabledChanged.emit()
        if not available and self._live_voice_active:
            self._live_voice_active = False
            self.liveVoiceActiveChanged.emit()
        if changed:
            self.liveAvailabilityChanged.emit()

    @Property(bool, notify=liveEnabledChanged)
    def liveEnabled(self) -> bool:
        return self._live_enabled

    def set_live_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled and self._live_available)
        if enabled == self._live_enabled:
            return
        self._live_enabled = enabled
        if not enabled and self._live_voice_active:
            self._live_voice_active = False
            self.liveVoiceActiveChanged.emit()
        self.liveEnabledChanged.emit()

    @Property(bool, notify=liveVoiceActiveChanged)
    def liveVoiceActive(self) -> bool:
        return self._live_voice_active

    def set_live_voice_active(self, active: bool) -> None:
        active = bool(active and self._live_enabled)
        if active == self._live_voice_active:
            return
        self._live_voice_active = active
        self.liveVoiceActiveChanged.emit()

    @Property(str, notify=liveSessionStateChanged)
    def liveSessionState(self) -> str:
        return self._live_session_state

    def set_live_session_state(self, state: str) -> None:
        normalized = str(state or "disconnected").strip().lower() or "disconnected"
        if normalized == self._live_session_state:
            return
        self._live_session_state = normalized
        self.liveSessionStateChanged.emit()

    @Property(float, notify=userAudioLevelChanged)
    def userAudioLevel(self) -> float:
        return self._user_audio_level

    def set_user_audio_level(self, level: float) -> None:
        normalized = max(0.0, min(1.0, float(level or 0.0)))
        if abs(normalized - self._user_audio_level) < 0.001:
            return
        self._user_audio_level = normalized
        self.userAudioLevelChanged.emit()

    @Property(float, notify=assistantAudioLevelChanged)
    def assistantAudioLevel(self) -> float:
        return self._assistant_audio_level

    def set_assistant_audio_level(self, level: float) -> None:
        normalized = max(0.0, min(1.0, float(level or 0.0)))
        if abs(normalized - self._assistant_audio_level) < 0.001:
            return
        self._assistant_audio_level = normalized
        self.assistantAudioLevelChanged.emit()

    @Property(bool, notify=expandedChanged)
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.expandedChanged.emit()

    @Property(bool, notify=backgroundHiddenChanged)
    def backgroundHidden(self) -> bool:
        return self._background_hidden

    def set_background_hidden(self, hidden: bool) -> None:
        hidden = bool(hidden)
        if hidden == self._background_hidden:
            return
        self._background_hidden = hidden
        self.backgroundHiddenChanged.emit()

    @Property(bool, notify=agentViewEnabledChanged)
    def agentViewEnabled(self) -> bool:
        return self._agent_view_enabled

    def set_agent_view_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        changed = False
        visible_changed = False
        old_visible = self.agent_view_visible()
        if enabled != self._agent_view_enabled:
            self._agent_view_enabled = enabled
            changed = True
        target_requested = self._agent_view_requested
        if not enabled:
            target_requested = False
        elif not target_requested:
            target_requested = True
        if target_requested != self._agent_view_requested:
            self._agent_view_requested = target_requested
            self.agentViewRequestedChanged.emit()
            changed = True
        if old_visible != self.agent_view_visible():
            visible_changed = True
        if changed:
            self.agentViewEnabledChanged.emit()
        if visible_changed:
            self.agentViewVisibleChanged.emit()

    @Property(bool, notify=agentViewRequestedChanged)
    def agentViewRequested(self) -> bool:
        return self._agent_view_requested

    def set_agent_view_requested(self, requested: bool) -> None:
        requested = bool(requested and self._agent_view_enabled)
        if requested == self._agent_view_requested:
            return
        old_visible = self.agent_view_visible()
        self._agent_view_requested = requested
        self.agentViewRequestedChanged.emit()
        if old_visible != self.agent_view_visible():
            self.agentViewVisibleChanged.emit()

    def toggle_agent_view_requested(self) -> None:
        self.set_agent_view_requested(not self._agent_view_requested)

    @Property(bool, notify=agentViewVisibleChanged)
    def agentViewVisible(self) -> bool:
        return self.agent_view_visible()

    def agent_view_visible(self) -> bool:
        return bool(self._agent_view_enabled and self._agent_view_requested)

    @Property(bool, notify=clickThroughEnabledChanged)
    def clickThroughEnabled(self) -> bool:
        return self._click_through_enabled

    def set_click_through_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._click_through_enabled:
            return
        self._click_through_enabled = enabled
        self.clickThroughEnabledChanged.emit()

    @Property(bool, notify=agentPreviewAvailableChanged)
    def agentPreviewAvailable(self) -> bool:
        return self._agent_preview_available

    def set_agent_preview_available(self, available: bool) -> None:
        available = bool(available)
        if available == self._agent_preview_available:
            return
        self._agent_preview_available = available
        self.agentPreviewAvailableChanged.emit()

    @Property(bool, notify=sidecarVisibleChanged)
    def sidecarVisible(self) -> bool:
        return self._sidecar_visible

    def set_sidecar_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._sidecar_visible:
            return
        self._sidecar_visible = visible
        self.sidecarVisibleChanged.emit()


@dataclass
class MessageEntry:
    id: str
    kind: str
    text: str
    speaker: str
    final: bool


class MessageFeedModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole + 1
    KindRole = IdRole + 1
    TextRole = KindRole + 1
    SpeakerRole = TextRole + 1
    FinalRole = SpeakerRole + 1

    countChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[MessageEntry] = []
        self._active_stream_rows: dict[str, int] = {}

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._entries)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._entries):
            return None
        entry = self._entries[row]
        if role == self.IdRole:
            return entry.id
        if role == self.KindRole:
            return entry.kind
        if role == self.TextRole:
            return entry.text
        if role == self.SpeakerRole:
            return entry.speaker
        if role == self.FinalRole:
            return entry.final
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.text
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.IdRole: b"id",
            self.KindRole: b"kind",
            self.TextRole: b"text",
            self.SpeakerRole: b"speaker",
            self.FinalRole: b"isFinal",
        }

    def clear(self) -> None:
        if not self._entries:
            return
        self.beginResetModel()
        self._entries.clear()
        self._active_stream_rows.clear()
        self.endResetModel()
        self.countChanged.emit()

    def entry_count(self) -> int:
        return len(self._entries)

    def entries_snapshot(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        entries = self._entries[-limit:] if isinstance(limit, int) and limit > 0 else self._entries
        return [
            {
                "id": entry.id,
                "kind": entry.kind,
                "text": entry.text,
                "speaker": entry.speaker,
                "final": entry.final,
            }
            for entry in entries
        ]

    def latest_entry_snapshot(self) -> dict[str, Any] | None:
        if not self._entries:
            return None
        return self.entries_snapshot(limit=1)[0]

    def _append_entry(self, *, kind: str, text: str, speaker: str = "", final: bool = True) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        row = len(self._entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(
            MessageEntry(
                id=str(uuid4()),
                kind=str(kind or "system").strip().lower() or "system",
                text=clean,
                speaker=str(speaker or "").strip().lower(),
                final=bool(final),
            )
        )
        self.endInsertRows()
        self.countChanged.emit()

    def add_system_message(self, text: str) -> None:
        self._append_entry(kind="system", text=text)

    def add_user_message(self, text: str) -> None:
        self._append_entry(kind="user", text=text, speaker="user")

    def add_output_message(self, text: str) -> None:
        self._append_entry(kind="output", text=text)

    def add_error_message(self, text: str) -> None:
        self._append_entry(kind="error", text=text)

    def add_activity_message(self, text: str) -> None:
        self._append_entry(kind="activity", text=text)

    def add_final_answer(self, text: str) -> None:
        self._append_entry(kind="assistant", text=text, speaker="assistant")

    def update_live_transcript(self, speaker: str, text: str, final: bool) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        speaker_key = str(speaker or "assistant").strip().lower() or "assistant"
        kind = "user" if speaker_key == "user" else "assistant"
        row = self._active_stream_rows.get(speaker_key)
        if row is None or row >= len(self._entries):
            self._append_entry(kind=kind, text=clean, speaker=speaker_key, final=final)
            row = len(self._entries) - 1
            if not final:
                self._active_stream_rows[speaker_key] = row
            return

        entry = self._entries[row]
        self._entries[row] = MessageEntry(
            id=entry.id,
            kind=kind,
            text=clean,
            speaker=speaker_key,
            final=bool(final),
        )
        model_index = self.index(row, 0)
        self.dataChanged.emit(
            model_index,
            model_index,
            [self.KindRole, self.TextRole, self.SpeakerRole, self.FinalRole],
        )
        if final:
            self._active_stream_rows.pop(speaker_key, None)
