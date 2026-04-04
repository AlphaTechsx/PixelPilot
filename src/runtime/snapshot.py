from __future__ import annotations

from typing import Any

from .auth import get_auth_state


def build_runtime_snapshot(
    *,
    state_store,
    message_feed_model,
    recent_action_updates: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = {
        "operationMode": state_store.operationMode,
        "visionMode": state_store.visionMode,
        "workspace": state_store.workspace,
        "liveAvailable": state_store.liveAvailable,
        "liveUnavailableReason": state_store.liveUnavailableReason,
        "liveEnabled": state_store.liveEnabled,
        "liveVoiceActive": state_store.liveVoiceActive,
        "liveSessionState": state_store.liveSessionState,
        "userAudioLevel": state_store.userAudioLevel,
        "assistantAudioLevel": state_store.assistantAudioLevel,
        "expanded": state_store.expanded,
        "backgroundHidden": state_store.backgroundHidden,
        "agentViewEnabled": state_store.agentViewEnabled,
        "agentViewRequested": state_store.agentViewRequested,
        "agentViewVisible": state_store.agentViewVisible,
        "clickThroughEnabled": state_store.clickThroughEnabled,
        "agentPreviewAvailable": state_store.agentPreviewAvailable,
        "sidecarVisible": state_store.sidecarVisible,
        "auth": get_auth_state(),
        "recentMessages": message_feed_model.entries_snapshot(limit=40),
        "recentActionUpdates": list(recent_action_updates or []),
    }
    if extra:
        snapshot.update(extra)
    return snapshot
