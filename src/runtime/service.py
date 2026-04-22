from __future__ import annotations

import logging
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer

from auth_manager import get_auth_manager
from config import Config
from doctor import run_doctor
from settings import discover_settings_paths
from uac.detection import get_uac_state_snapshot
from uac.flow import (
    get_external_uac_mode,
    get_uac_flow_progress,
    get_uac_queue_gate,
    set_external_uac_mode,
)
from .auth import (
    exchange_desktop_code,
    get_auth_state,
    logout_all,
    save_api_key,
    start_browser_flow,
)
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
        self._last_doctor_report: dict[str, Any] | None = None
        self._settings_watch_signature: tuple[tuple[str, int, int], ...] | None = None
        self._settings_watch_pending_signature: tuple[tuple[str, int, int], ...] | None = None
        self._settings_watch_pending_since = 0.0
        self._settings_watch_timer = QTimer(self)
        self._settings_watch_timer.setInterval(2_000)
        self._settings_watch_timer.timeout.connect(self._check_settings_files)

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
        self._settings_watch_timer.start()
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
            self.state_store.liveStatusChanged,
            self.state_store.wakeWordEnabledChanged,
            self.state_store.wakeWordStateChanged,
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

    def _session_store(self):
        agent = getattr(self.controller, "agent", None)
        return getattr(agent, "session_store", None)

    def _runtime_settings_sources(self) -> list[str]:
        agent = getattr(self.controller, "agent", None)
        settings = getattr(agent, "runtime_settings", None)
        return [str(path) for path in list(getattr(settings, "sources", []) or [])]

    def _runtime_settings_validation_errors(self) -> list[dict[str, Any]]:
        agent = getattr(self.controller, "agent", None)
        settings = getattr(agent, "runtime_settings", None)
        if settings is None:
            return []
        if hasattr(settings, "validation_error_dicts"):
            try:
                return list(settings.validation_error_dicts())
            except Exception:
                logger.debug("Failed to read settings validation errors", exc_info=True)
        return [
            item.as_dict() if hasattr(item, "as_dict") else dict(item)
            for item in list(getattr(settings, "validation_errors", []) or [])
            if isinstance(item, dict) or hasattr(item, "as_dict")
        ]

    def _session_directory(self) -> str:
        store = self._session_store()
        return str(getattr(store, "root_dir", "") or "")

    def _latest_session_context(self) -> dict[str, Any]:
        store = self._session_store()
        if store is None:
            return {"available": False}
        latest = store.latest_context()
        return latest.as_dict() if hasattr(latest, "as_dict") else {"available": False}

    def _extension_summary(self) -> dict[str, Any]:
        agent = getattr(self.controller, "agent", None)
        manager = getattr(agent, "extension_manager", None)
        if manager is None:
            return {"pluginCount": 0, "mcpServerCount": 0, "toolCount": 0, "toolNames": []}
        try:
            return dict(manager.summary())
        except Exception:
            return {"pluginCount": 0, "mcpServerCount": 0, "toolCount": 0, "toolNames": []}

    def _voiceprint_status(self) -> dict[str, Any]:
        status = getattr(self.controller, "voiceprint_status", None)
        if not callable(status):
            return {
                "enabled": False,
                "enrolled": False,
                "available": True,
                "status": "unavailable",
                "lastScore": None,
                "threshold": Config.VOICEPRINT_THRESHOLD,
                "sampleCount": 0,
                "unavailableReason": "Voiceprint service is unavailable.",
            }
        try:
            return dict(status())
        except Exception as exc:
            logger.debug("Failed to build voiceprint status", exc_info=True)
            return {
                "enabled": False,
                "enrolled": False,
                "available": False,
                "status": "unavailable",
                "lastScore": None,
                "threshold": Config.VOICEPRINT_THRESHOLD,
                "sampleCount": 0,
                "unavailableReason": str(exc),
            }

    def _settings_signature(self) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for path in discover_settings_paths(Config.PROJECT_ROOT):
            try:
                stat = path.stat()
            except FileNotFoundError:
                signature.append((str(path), -1, -1))
                continue
            except Exception:
                signature.append((str(path), -2, -2))
                continue
            signature.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(signature)

    def _check_settings_files(self) -> None:
        agent = getattr(self.controller, "agent", None)
        if agent is None:
            return
        signature = self._settings_signature()
        if self._settings_watch_signature is None:
            self._settings_watch_signature = signature
            return
        if signature == self._settings_watch_signature:
            self._settings_watch_pending_signature = None
            self._settings_watch_pending_since = 0.0
            return

        now = time.monotonic()
        if signature != self._settings_watch_pending_signature:
            self._settings_watch_pending_signature = signature
            self._settings_watch_pending_since = now
            return
        if now - self._settings_watch_pending_since < 1.0:
            return

        self._settings_watch_signature = signature
        self._settings_watch_pending_signature = None
        self._settings_watch_pending_since = 0.0
        self._reload_runtime_settings()

    def _reload_runtime_settings(self) -> None:
        agent = getattr(self.controller, "agent", None)
        if agent is None:
            return
        try:
            reload_settings = getattr(agent, "reload_runtime_settings", None)
            if callable(reload_settings):
                reload_settings()
            live_session = getattr(self.controller, "live_session", None)
            tools = getattr(live_session, "tools", None)
            refresh_tools = getattr(tools, "refresh_runtime_settings", None)
            if callable(refresh_tools):
                refresh_tools()
            self.adapter.add_activity_message("Runtime settings reloaded.")
            self.bridge_server.publish_state_updated()
        except Exception:
            logger.warning("Failed to reload runtime settings after file change.", exc_info=True)

    def _build_snapshot(self) -> dict[str, Any]:
        return build_runtime_snapshot(
            state_store=self.state_store,
            message_feed_model=self.message_feed_model,
            recent_action_updates=self._recent_action_updates(),
            extra={
                "latestSessionContext": self._latest_session_context(),
                "extensions": self._extension_summary(),
                "voiceprint": self._voiceprint_status(),
                "settingsSources": self._runtime_settings_sources(),
                "settingsValidationErrors": self._runtime_settings_validation_errors(),
                "sessionDirectory": self._session_directory(),
                "lastDoctorReport": dict(self._last_doctor_report or {}),
            },
        )

    def _handle_command(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(method or "").strip()
        body = dict(payload or {})
        logger.debug("Runtime command: %s", command)

        if command == "auth.getStatus":
            return {"auth": get_auth_state()}
        if command == "auth.startBrowserFlow":
            return self._auth_start_browser_flow(body)
        if command == "auth.login":
            return self._auth_login(body)
        if command == "auth.exchangeDesktopCode":
            return self._auth_exchange_desktop_code(body)
        if command == "auth.useApiKey":
            return self._auth_use_api_key(body)
        if command == "auth.logout":
            return self._auth_logout()
        if command == "ollama.listModels":
            return self._ollama_list_models(body)
        if command == "live.submitText":
            return self.controller.handle_user_command(body.get("text"))
        if command == "doctor.run":
            report = run_doctor(
                agent=getattr(self.controller, "agent", None),
                controller=self.controller,
                runtime_service=self,
            )
            self._last_doctor_report = report.as_dict()
            self.bridge_server.publish_state_updated()
            return {
                "doctor": report.as_dict(),
                "text": report.render_text(),
            }
        if command == "session.getLatestContext":
            return {"session": self._latest_session_context()}
        if command == "session.resumeLatestContext":
            store = self._session_store()
            if store is None:
                return {"session": {"available": False}}
            latest = store.resume_latest_context()
            payload = latest.as_dict() if hasattr(latest, "as_dict") else {"available": False}
            if bool(payload.get("available")):
                summary = str(payload.get("summaryText") or "").strip()
                if summary:
                    self.adapter.add_activity_message("Previous session context is available for manual resume.")
                    self.adapter.add_system_message(summary)
            self.bridge_server.publish_state_updated()
            return {"session": payload}
        if command == "session.openFolder":
            directory = self._session_directory()
            if not directory:
                raise RuntimeError("Session log directory is unavailable.")
            self._open_folder(directory)
            return {"opened": True, "path": directory}
        if command == "extensions.getSummary":
            return {"extensions": self._extension_summary()}
        if command == "extensions.reload":
            agent = getattr(self.controller, "agent", None)
            manager = getattr(agent, "extension_manager", None)
            if manager is None:
                return {"extensions": self._extension_summary()}
            manager.reload()
            self.bridge_server.publish_state_updated()
            return {"extensions": self._extension_summary()}
        if command == "live.setEnabled":
            self.controller.handle_live_mode_changed(bool(body.get("enabled")))
            return {
                "liveEnabled": self.state_store.liveEnabled,
                "liveSessionState": self.state_store.liveSessionState,
            }
        if command == "live.setVoice":
            self.controller.handle_live_voice_toggled(bool(body.get("enabled")))
            return {"liveVoiceActive": self.state_store.liveVoiceActive}
        if command == "wakeWord.setEnabled":
            self.controller.handle_wake_word_toggled(bool(body.get("enabled")))
            return {
                "wakeWordEnabled": self.state_store.wakeWordEnabled,
                "wakeWordState": self.state_store.wakeWordState,
                "wakeWordUnavailableReason": self.state_store.wakeWordUnavailableReason,
            }
        if command == "voiceprint.getStatus":
            return {"voiceprint": self._voiceprint_status()}
        if command == "voiceprint.setEnabled":
            status = self.controller.voiceprint_set_enabled(bool(body.get("enabled")))
            self.bridge_server.publish_state_updated()
            return {"voiceprint": status}
        if command == "voiceprint.recordSample":
            seconds = float(body.get("seconds") or 2.0)
            status = self.controller.voiceprint_record_sample(seconds=seconds)
            self.bridge_server.publish_state_updated()
            return status
        if command == "voiceprint.completeEnrollment":
            status = self.controller.voiceprint_complete_enrollment()
            self.bridge_server.publish_state_updated()
            return {"voiceprint": status}
        if command == "voiceprint.clear":
            status = self.controller.voiceprint_clear()
            self.bridge_server.publish_state_updated()
            return {"voiceprint": status}
        if command == "uac.getState":
            return {
                "uac": get_uac_state_snapshot(),
                "uacGate": get_uac_queue_gate(),
            }
        if command == "uac.getProgress":
            return {
                "uac": get_uac_flow_progress(),
                "uacGate": get_uac_queue_gate(),
            }
        if command == "uac.getMode":
            return {
                "uacMode": get_external_uac_mode(),
                "uacGate": get_uac_queue_gate(),
            }
        if command == "uac.setMode":
            if "active" not in body:
                raise RuntimeError("uac.setMode requires an 'active' boolean.")
            logger.info(
                "Runtime command uac.setMode active=%s source=%s",
                bool(body.get("active")),
                str(body.get("source") or "external_detector").strip() or "external_detector",
            )
            mode_state = set_external_uac_mode(
                bool(body.get("active")),
                source=str(body.get("source") or "external_detector"),
                message=str(body.get("message") or ""),
                prompt=body.get("prompt") if isinstance(body.get("prompt"), dict) else None,
            )
            return {
                "uacMode": mode_state,
                "uacGate": get_uac_queue_gate(),
            }
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

    @staticmethod
    def _open_folder(target: str) -> None:
        path = Path(str(target or "")).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Folder does not exist: {path}")
        try:
            os.startfile(str(path))
            return
        except AttributeError:
            pass
        except OSError:
            pass
        subprocess.run(["explorer", str(path)], check=False)

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

    def _auth_start_browser_flow(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "signin").strip().lower() or "signin"
        result = start_browser_flow(mode)
        return {"authUrl": result["url"], "state": result["state"], "mode": result["mode"]}

    def _auth_exchange_desktop_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip()
        state = str(payload.get("state") or "").strip()
        auth_state = exchange_desktop_code(code, state)
        self.controller.refresh_live_runtime()
        self.bridge_server.publish_event("auth.changed", {"auth": auth_state})
        self.bridge_server.publish_state_updated()
        return {"auth": auth_state}

    def _auth_use_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_state = save_api_key(
            str(payload.get("apiKey") or ""),
            provider_id=str(payload.get("provider") or ""),
            base_url=str(payload.get("baseUrl") or ""),
            model=str(payload.get("model") or ""),
        )
        self.controller.refresh_live_runtime()
        self.bridge_server.publish_event("auth.changed", {"auth": auth_state})
        self.bridge_server.publish_state_updated()
        return {"auth": auth_state}

    def _auth_logout(self) -> dict[str, Any]:
        auth_state = logout_all()
        self.bridge_server.publish_event("auth.changed", {"auth": auth_state})
        self.bridge_server.publish_state_updated()
        QTimer.singleShot(0, self.controller.refresh_live_runtime)
        return {"auth": auth_state}

    def _ollama_list_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        import json
        from urllib import request
        base_url = str(payload.get("baseUrl") or Config.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")
        url = f"{base_url}/api/tags"
        try:
            with request.urlopen(url, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [str(item["name"]) for item in data.get("models", [])]
                return {"models": sorted(models)}
        except Exception as exc:
            logger.debug("Failed to list Ollama models at %s: %s", url, exc)
            return {"models": [], "error": str(exc)}
