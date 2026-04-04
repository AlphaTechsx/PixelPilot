from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from PySide6.QtCore import QObject, QTimer

from auth_manager import get_auth_manager
from config import Config
from .auth import get_auth_state, logout_all, save_api_key
from .snapshot import build_runtime_snapshot


logger = logging.getLogger("pixelpilot.runtime.service")


class ElectronRuntimeService(QObject):
    def __init__(
        self,
        *,
        app,
        controller,
        adapter,
        state_store,
        message_feed_model,
        bridge_server,
        shell_proxy,
    ) -> None:
        super().__init__()
        self.app = app
        self.controller = controller
        self.adapter = adapter
        self.state_store = state_store
        self.message_feed_model = message_feed_model
        self.bridge_server = bridge_server
        self.shell_proxy = shell_proxy

        self.bridge_server.set_snapshot_provider(self._build_snapshot)
        self.bridge_server.set_command_handler(self._handle_command)

        self._connect_state_publishers()

    @staticmethod
    def resolve_bridge_settings() -> tuple[str, int, str]:
        host = str(os.environ.get("PIXELPILOT_ELECTRON_BRIDGE_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(os.environ.get("PIXELPILOT_ELECTRON_BRIDGE_PORT", "8766"))
        except Exception:
            port = 8766
        token = str(os.environ.get("PIXELPILOT_ELECTRON_BRIDGE_TOKEN", "")).strip() or secrets.token_urlsafe(24)
        return host, port, token

    def start(self) -> None:
        self.bridge_server.start()
        self.bridge_server.set_runtime_ready(True)
        self.adapter.add_activity_message("Electron runtime bridge online.")
        QTimer.singleShot(0, self.controller.start_bootstrap)

    def _connect_state_publishers(self) -> None:
        structural_signals = [
            self.state_store.operationModeChanged,
            self.state_store.visionModeChanged,
            self.state_store.workspaceChanged,
            self.state_store.liveAvailabilityChanged,
            self.state_store.liveEnabledChanged,
            self.state_store.liveVoiceActiveChanged,
            self.state_store.liveSessionStateChanged,
            self.state_store.expandedChanged,
            self.state_store.backgroundHiddenChanged,
            self.state_store.agentViewEnabledChanged,
            self.state_store.agentViewRequestedChanged,
            self.state_store.agentViewVisibleChanged,
            self.state_store.clickThroughEnabledChanged,
            self.state_store.agentPreviewAvailableChanged,
            self.state_store.sidecarVisibleChanged,
        ]
        for signal in structural_signals:
            signal.connect(self.bridge_server.publish_state_updated)

        self.message_feed_model.countChanged.connect(self._publish_latest_message)

    def _publish_latest_message(self) -> None:
        latest = self.message_feed_model.latest_entry_snapshot()
        if latest is None:
            return
        self.bridge_server.publish_event("message.appended", {"entry": latest})

    def _recent_action_updates(self) -> list[dict[str, Any]]:
        live_session = getattr(self.controller, "live_session", None)
        if live_session is None:
            return []
        updates = list(getattr(live_session, "_recent_action_updates", []) or [])
        return [dict(item) for item in updates if isinstance(item, dict)]

    def _build_snapshot(self) -> dict[str, Any]:
        return build_runtime_snapshot(
            state_store=self.state_store,
            message_feed_model=self.message_feed_model,
            recent_action_updates=self._recent_action_updates(),
        )

    def _handle_command(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(method or "").strip()
        body = dict(payload or {})
        logger.debug("Runtime command: %s", command)

        if command == "auth.getStatus":
            return {"auth": get_auth_state()}
        if command == "auth.login":
            return self._auth_login(body)
        if command == "auth.useApiKey":
            return self._auth_use_api_key(body)
        if command == "auth.logout":
            return self._auth_logout()
        if command == "live.submitText":
            return self.controller.handle_user_command(body.get("text"))
        if command == "live.setEnabled":
            self.controller.handle_live_mode_changed(bool(body.get("enabled")))
            return {"liveEnabled": self.state_store.liveEnabled}
        if command == "live.setVoice":
            self.controller.handle_live_voice_toggled(bool(body.get("enabled")))
            return {"liveVoiceActive": self.state_store.liveVoiceActive}
        if command == "live.stop":
            self.controller.stop_current_turn()
            return {"stopped": True}
        if command == "mode.set":
            self.controller.handle_mode_changed(Config.get_mode(body.get("value")))
            return {"operationMode": self.state_store.operationMode}
        if command == "vision.set":
            self.controller.handle_vision_changed(str(body.get("value") or "ocr").lower())
            return {"visionMode": self.state_store.visionMode}
        if command == "workspace.set":
            self.controller.handle_workspace_changed(str(body.get("value") or "user"))
            return {"workspace": self.state_store.workspace}
        if command == "agentView.setRequested":
            self.adapter.set_agent_view_requested(bool(body.get("requested")))
            self.shell_proxy.refresh_agent_preview_visibility()
            return {"agentViewRequested": self.state_store.agentViewRequested}
        if command == "shell.setExpanded":
            self.adapter.set_expanded(bool(body.get("expanded")))
            self.shell_proxy.refresh_agent_preview_visibility()
            return {"expanded": self.state_store.expanded}
        if command == "shell.setBackgroundHidden":
            self.adapter.set_background_hidden(bool(body.get("hidden")))
            self.shell_proxy.refresh_agent_preview_visibility()
            return {"backgroundHidden": self.state_store.backgroundHidden}
        if command == "runtime.shutdown":
            QTimer.singleShot(0, self.app.quit)
            return {"shuttingDown": True}
        raise RuntimeError(f"Unsupported runtime command: {command}")

    def _auth_login(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = str(payload.get("email") or "").strip()
        password = str(payload.get("password") or "")
        if not email or not password:
            raise RuntimeError("Please enter email and password.")

        auth = get_auth_manager()
        auth.login(email, password)
        self.controller.refresh_live_runtime()
        self.bridge_server.publish_event("auth.changed", {"auth": get_auth_state()})
        self.bridge_server.publish_state_updated()
        return {"auth": get_auth_state()}

    def _auth_use_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_state = save_api_key(str(payload.get("apiKey") or ""))
        self.controller.refresh_live_runtime()
        self.bridge_server.publish_event("auth.changed", {"auth": auth_state})
        self.bridge_server.publish_state_updated()
        return {"auth": auth_state}

    def _auth_logout(self) -> dict[str, Any]:
        auth_state = logout_all()
        self.controller.refresh_live_runtime()
        self.bridge_server.publish_event("auth.changed", {"auth": auth_state})
        self.bridge_server.publish_state_updated()
        return {"auth": auth_state}
