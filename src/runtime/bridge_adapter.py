from __future__ import annotations

from typing import Any

from runtime.adapter import RuntimeAdapter


class ElectronBridgeAdapter(RuntimeAdapter):
    def __init__(
        self,
        *,
        bridge_server,
        ui_state_store=None,
        message_feed_model=None,
        request_timeout_s: float = 60.0,
    ):
        super().__init__(ui_state_store=ui_state_store, message_feed_model=message_feed_model)
        self.bridge_server = bridge_server
        self.request_timeout_s = max(1.0, float(request_timeout_s or 60.0))

    def _request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.bridge_server.request_ui(
            method,
            payload,
            timeout_s=self.request_timeout_s,
            allow_missing=True,
        )
        return dict(response or {})

    def update_live_transcript(self, speaker: str, text: str, final: bool):
        super().update_live_transcript(speaker, text, final)
        self.bridge_server.publish_event(
            "live.transcript",
            {
                "speaker": str(speaker or ""),
                "text": str(text or ""),
                "final": bool(final),
            },
        )

    def update_live_session_state(self, state: str):
        super().update_live_session_state(state)
        self.bridge_server.publish_event(
            "live.sessionState",
            {
                "state": str(state or "disconnected"),
            },
        )

    def update_live_action_state(self, payload: dict):
        super().update_live_action_state(payload)
        self.bridge_server.publish_event("live.actionState", dict(payload or {}))

    def update_live_audio_level(self, level: float):
        super().update_live_audio_level(level)
        self.bridge_server.publish_event(
            "live.audioLevel",
            {
                "channel": "user",
                "level": float(level or 0.0),
            },
        )

    def update_assistant_audio_level(self, level: float):
        super().update_assistant_audio_level(level)
        self.bridge_server.publish_event(
            "live.audioLevel",
            {
                "channel": "assistant",
                "level": float(level or 0.0),
            },
        )

    def update_live_availability(self, available: bool, reason: str):
        super().update_live_availability(available, reason)
        self.bridge_server.publish_event(
            "live.availability",
            {
                "available": bool(available),
                "reason": str(reason or ""),
            },
        )

    def update_live_voice_active(self, active: bool):
        super().update_live_voice_active(active)
        self.bridge_server.publish_event(
            "live.voiceActive",
            {
                "active": bool(active),
            },
        )

    def ask_confirmation(self, title, text):
        response = self._request_ui(
            "ui.requestConfirmation",
            {
                "title": str(title or "Confirm"),
                "text": str(text or ""),
            },
        )
        return bool(response.get("approved") or response.get("result"))

    def prepare_for_screenshot(self):
        return self._request_ui("ui.prepareForScreenshot", {})

    def restore_after_screenshot(self, payload: dict | None = None):
        return self._request_ui("ui.restoreAfterScreenshot", dict(payload or {}))

    def set_click_through(self, enable):
        return self._request_ui(
            "shell.setClickThrough",
            {
                "enabled": bool(enable),
            },
        )
