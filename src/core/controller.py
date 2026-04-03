import logging
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Slot
from PySide6.QtWidgets import QDialog

from config import Config, OperationMode
from ui.custom_dialogs import ConfirmationDialog

logger = logging.getLogger("pixelpilot.controller")
startup_logger = logging.getLogger("pixelpilot.startup")


class MainController(QObject):
    def __init__(self, gui_adapter, main_window, *, startup_started_at: float | None = None):
        super().__init__()
        self.gui_adapter = gui_adapter
        self.main_window = main_window
        self.agent = None
        self.live_session = None
        self.live_mode_enabled = False
        self._live_action_passthrough_active = False
        self.desktop_manager = None
        self.gateway_server = None
        self.gateway_thread = None
        self._bootstrap_started = False
        self._startup_started_at = float(startup_started_at or time.perf_counter())
        self._startup_logged_phases: set[str] = set()
        self._app_index_watch_timer = QTimer(self)
        self._app_index_watch_timer.setInterval(250)
        self._app_index_watch_timer.timeout.connect(self._check_app_index_ready)

        if getattr(self.gui_adapter, "current_mode", None) is None:
            self.gui_adapter.current_mode = Config.DEFAULT_MODE

        self.gui_adapter.confirmation_requested.connect(self.handle_confirmation)
        self.gui_adapter.screenshot_prep_requested.connect(self.handle_screenshot_prep)
        self.gui_adapter.screenshot_restore_requested.connect(self.handle_screenshot_restore)
        self.gui_adapter.click_through_requested.connect(self.handle_click_through)
        self.gui_adapter.workspace_changed.connect(self.handle_workspace_changed)

    def mark_startup_phase(self, phase: str, *, status: str = "ok", detail: str = "") -> None:
        if phase in self._startup_logged_phases:
            return

        elapsed_ms = int((time.perf_counter() - self._startup_started_at) * 1000)
        clean_detail = " ".join(str(detail or "").split())
        if clean_detail:
            startup_logger.info(
                "STARTUP phase=%s status=%s elapsed_ms=%d detail=%s",
                phase,
                status,
                elapsed_ms,
                clean_detail,
            )
        else:
            startup_logger.info(
                "STARTUP phase=%s status=%s elapsed_ms=%d",
                phase,
                status,
                elapsed_ms,
            )
        self._startup_logged_phases.add(phase)

    def start_bootstrap(self) -> None:
        if self._bootstrap_started:
            return
        self._bootstrap_started = True
        QTimer.singleShot(0, self._bootstrap_agent_core)

    def init_agent(self) -> None:
        self.start_bootstrap()

    @staticmethod
    def _normalize_workspace(workspace: str) -> str:
        key = (workspace or "user").strip().lower() or "user"
        if key not in {"user", "agent"}:
            key = "user"
        return key

    @staticmethod
    def _apply_vision_flags(mode_key: str) -> None:
        if mode_key == "robo":
            Config.USE_ROBOTICS_EYE = True
            Config.LAZY_VISION = False
            return

        Config.USE_ROBOTICS_EYE = False
        Config.LAZY_VISION = True

    def _resolve_current_workspace(self) -> str:
        if not self.agent:
            return "user"
        return self._normalize_workspace(getattr(self.agent, "active_workspace", "user"))

    def _resolve_current_mode(self) -> OperationMode:
        mode = getattr(self.agent, "mode", None) if self.agent else None
        if mode is None:
            mode = getattr(self.gui_adapter, "current_mode", None)
        if isinstance(mode, OperationMode):
            return mode
        mode_value = getattr(mode, "value", mode)
        return Config.get_mode(str(mode_value or Config.DEFAULT_MODE.value))

    def _is_live_session_enabled(self) -> bool:
        return bool(
            self.live_mode_enabled
            and self.live_session
            and bool(getattr(self.live_session, "enabled", False))
        )

    def _apply_click_through_policy(self) -> None:
        click_through = bool(
            self._resolve_current_workspace() == "user"
            and self._is_live_session_enabled()
            and self._live_action_passthrough_active
        )
        try:
            self.main_window.set_click_through_enabled(click_through)
            self.gui_adapter.set_click_through_enabled(click_through)
        except Exception:
            pass

    def _startup_message(self, component: str, *, unavailable: bool = False) -> str:
        if not self._bootstrap_started:
            return f"{component} is unavailable."

        phase = "agent_core_ready" if component == "AI" else "live_ready"
        if phase not in self._startup_logged_phases:
            return f"{component} is still starting up."
        if unavailable:
            return f"{component} is unavailable."
        return f"{component} is unavailable."

    def _apply_default_live_mode(self) -> None:
        if not self.live_session:
            self.live_mode_enabled = False
            return

        available = bool(getattr(self.live_session, "is_available", False))
        should_enable = bool(Config.LIVE_MODE_DEFAULT_ENABLED and available)
        self.handle_live_mode_changed(should_enable)
        if should_enable and Config.LIVE_MODE_DEFAULT_VOICE_ENABLED:
            self.handle_live_voice_toggled(True)

    def _create_robotics_eye(self):
        if not Config.USE_ROBOTICS_EYE:
            return None

        from tools.eye import GeminiRoboticsEye

        return GeminiRoboticsEye()

    def _sync_window_from_agent(self) -> None:
        if not self.agent:
            return

        try:
            self.gui_adapter.set_operation_mode(self.agent.mode)
            self.gui_adapter.set_vision_mode("ROBO" if self.agent.robotics_eye else "OCR")
            self.gui_adapter.set_workspace(self.agent.active_workspace)
            self.gui_adapter.set_agent_view_enabled(self.agent.active_workspace == "agent")
        except Exception:
            pass

    def _bootstrap_agent_core(self) -> None:
        try:
            robotics_eye = None
            if Config.USE_ROBOTICS_EYE:
                try:
                    robotics_eye = self._create_robotics_eye()
                except Exception as exc:
                    Config.USE_ROBOTICS_EYE = False
                    Config.LAZY_VISION = True
                    self.gui_adapter.add_error_message(
                        f"Robotics vision unavailable, falling back to OCR: {exc}"
                    )

            from agent.core import AgentOrchestrator

            self.agent = AgentOrchestrator(
                mode=self._resolve_current_mode(),
                chat_window=self.gui_adapter,
                robotics_eye=robotics_eye,
            )
            self.gui_adapter.current_mode = self.agent.mode

            if self.desktop_manager:
                self.agent.desktop_manager = self.desktop_manager
                if hasattr(self.agent.keyboard, "set_desktop_manager"):
                    self.agent.keyboard.set_desktop_manager(self.desktop_manager)

            self._sync_window_from_agent()
            self.update_sidecar_visibility()
            self.mark_startup_phase(
                "agent_core_ready",
                detail=f"mode={self.agent.mode.value}",
            )
        except Exception as exc:
            logger.exception("Failed to initialize agent core")
            self.mark_startup_phase("agent_core_ready", status="error", detail=str(exc))
            self.gui_adapter.add_error_message(f"Failed to initialize agent: {exc}")
            return

        QTimer.singleShot(0, self._bootstrap_live_runtime)

    def _bootstrap_live_runtime(self) -> None:
        if not self.agent:
            self.mark_startup_phase("live_ready", status="error", detail="agent_unavailable")
            return

        self._init_live_session()

        if not self.live_session:
            self.mark_startup_phase("live_ready", status="error", detail="session_unavailable")
        else:
            available = bool(getattr(self.live_session, "is_available", False))
            reason = str(getattr(self.live_session, "unavailable_reason", "") or "")
            self.mark_startup_phase(
                "live_ready",
                status="ok" if available else "unavailable",
                detail="available=true" if available else (reason or "live_unavailable"),
            )

        QTimer.singleShot(0, self._bootstrap_app_index)

    def _bootstrap_app_index(self) -> None:
        if not self.agent:
            self.mark_startup_phase("app_index_ready", status="error", detail="agent_unavailable")
            return

        service = getattr(self.agent, "app_indexer", None)
        if service is None:
            self.mark_startup_phase("app_index_ready", status="error", detail="service_unavailable")
            return

        service.start_warmup()
        self._check_app_index_ready()
        if service.state == "loading":
            self._app_index_watch_timer.start()

    def _check_app_index_ready(self) -> None:
        if "app_index_ready" in self._startup_logged_phases:
            self._app_index_watch_timer.stop()
            return

        if not self.agent:
            self._app_index_watch_timer.stop()
            return

        service = getattr(self.agent, "app_indexer", None)
        if service is None:
            self._app_index_watch_timer.stop()
            self.mark_startup_phase("app_index_ready", status="error", detail="service_unavailable")
            return

        state = getattr(service, "state", "idle")
        if state == "ready":
            self._app_index_watch_timer.stop()
            self.mark_startup_phase(
                "app_index_ready",
                detail=f"apps={service.app_count}",
            )
        elif state == "error":
            self._app_index_watch_timer.stop()
            self.mark_startup_phase(
                "app_index_ready",
                status="error",
                detail=getattr(service, "error", "") or "warmup_failed",
            )

    def _init_live_session(self) -> None:
        if self.live_session:
            try:
                self.live_session.shutdown()
            except Exception:
                pass
            self.live_session = None

        try:
            from live.session import LiveSessionManager

            self.live_session = LiveSessionManager(agent=self.agent)
            self.live_session.transcript_received.connect(self._handle_live_transcript)
            self.live_session.session_state_changed.connect(self._handle_live_session_state)
            self.live_session.action_state_changed.connect(self._handle_live_action_state)
            self.live_session.error_received.connect(self._handle_live_error)
            self.live_session.audio_level_changed.connect(self._handle_live_audio_level)
            self.live_session.assistant_audio_level_changed.connect(
                self._handle_live_assistant_audio_level
            )
            self.live_session.availability_changed.connect(self._handle_live_availability)
            self.live_session.voice_active_changed.connect(self._handle_live_voice_active)

            available = bool(getattr(self.live_session, "is_available", False))
            reason = str(getattr(self.live_session, "unavailable_reason", "") or "")
            self.live_session.notify_mode_changed(self.agent.mode)
            self._handle_live_availability(available, reason)
            self._handle_live_session_state("disconnected")
            self._apply_default_live_mode()
            self._init_gateway()
        except Exception as exc:
            logger.exception("Failed to initialize Gemini Live session")
            self.live_session = None
            self.live_mode_enabled = False
            self._handle_live_availability(False, str(exc))

    def _init_gateway(self) -> None:
        if not Config.ENABLE_GATEWAY:
            return
        if not self.live_session:
            return

        try:
            from services.gateway import GatewayServer

            if self.gateway_server is None:
                self.gateway_server = GatewayServer(live_session=self.live_session)
            else:
                self.gateway_server.attach_live_session(self.live_session)

            if self.gateway_thread and self.gateway_thread.is_alive():
                return

            self.gateway_thread = threading.Thread(
                target=self.gateway_server.start,
                name="PixelPilotGateway",
                daemon=True,
            )
            self.gateway_thread.start()
            self.gui_adapter.add_activity_message(
                f"Gateway listening on ws://{Config.GATEWAY_HOST}:{Config.GATEWAY_PORT}"
            )
            if not Config.GATEWAY_TOKEN:
                self.gui_adapter.add_error_message(
                    "Gateway started without PIXELPILOT_GATEWAY_TOKEN. Set a token to require authentication."
                )
        except Exception as exc:
            logger.exception("Failed to initialize gateway")
            self.gui_adapter.add_error_message(f"Failed to start gateway: {exc}")

    def init_sidecar(self):
        if not Config.ENABLE_AGENT_DESKTOP:
            return

        try:
            from desktop.desktop_manager import AgentDesktopManager

            if self.desktop_manager and getattr(self.desktop_manager, "is_created", False):
                self.main_window.attach_agent_preview_source(self.desktop_manager)
                return

            self.desktop_manager = AgentDesktopManager(Config.AGENT_DESKTOP_NAME)
            if not self.desktop_manager.create_desktop():
                logger.warning("Failed to create Agent Desktop")
                self.desktop_manager = None
                self.main_window.attach_agent_preview_source(None)
                return

            self.desktop_manager.initialize_shell()

            self.main_window.attach_agent_preview_source(self.desktop_manager)

            if self.agent:
                self.agent.desktop_manager = self.desktop_manager
                if hasattr(self.agent.keyboard, "set_desktop_manager"):
                    self.agent.keyboard.set_desktop_manager(self.desktop_manager)

            logger.info("Agent Desktop initialized for sidecar preview")
            self.update_sidecar_visibility()
        except Exception as exc:
            logger.exception("Failed to initialize Agent Desktop: %s", exc)
            self.desktop_manager = None
            self.main_window.attach_agent_preview_source(None)

    @Slot(str, str, object)
    def handle_confirmation(self, title, text, payload):
        dialog = ConfirmationDialog(self.main_window.dialog_parent(), title, text)
        payload["result"] = dialog.exec() == QDialog.DialogCode.Accepted
        payload["event"].set()

    @Slot(object)
    def handle_screenshot_prep(self, payload):
        state = self.main_window.prepare_for_screenshot()
        payload.update(state)
        QCoreApplication.processEvents()
        payload["event"].set()

    @Slot(object)
    def handle_screenshot_restore(self, payload):
        self.main_window.restore_after_screenshot(payload)
        payload["event"].set()

    @Slot(bool, object)
    def handle_click_through(self, enable, payload):
        try:
            self.main_window.set_click_through_enabled(bool(enable))
            self.gui_adapter.set_click_through_enabled(bool(enable))
        except Exception:
            pass
        payload["event"].set()

    def handle_user_command(self, text):
        clean = str(text or "").strip()
        if not clean:
            return
        if not self.agent:
            self.gui_adapter.add_error_message(self._startup_message("AI"))
            return
        if not self.live_session:
            self.gui_adapter.add_error_message(self._startup_message("Gemini Live"))
            return
        if not self.live_mode_enabled or not self.live_session.enabled:
            self.gui_adapter.add_error_message("AI is off. Turn AI on to send instructions.")
            return
        result = self.live_session.submit_text(clean)
        if not isinstance(result, dict):
            return
        if not bool(result.get("ok", False)):
            message = str(result.get("message") or "Failed to send input to Gemini Live.").strip()
            if message:
                self.gui_adapter.add_error_message(message)
            return
        status = str(result.get("status") or "").strip().lower()
        message = str(result.get("message") or "").strip()
        if status in {"nudge_queued", "nudge_sent"} and message:
            self.gui_adapter.add_activity_message(message)

    def stop_current_turn(self):
        if self.live_mode_enabled and self.live_session and self.live_session.enabled:
            self.gui_adapter.add_activity_message("Stopping...")
            self.live_session.request_stop()
            return
        self.gui_adapter.add_activity_message("Nothing to stop")

    def toggle_click_through(self):
        try:
            current = bool(self.main_window.click_through_enabled())
            self.main_window.set_click_through_enabled(not current)
            self.gui_adapter.set_click_through_enabled(not current)
        except Exception as exc:
            self.gui_adapter.add_error_message(
                f"Failed to toggle interactivity: {exc}"
            )

    def shutdown(self):
        try:
            self._app_index_watch_timer.stop()

            if self.live_session:
                try:
                    self.live_session.shutdown()
                except Exception:
                    pass

            if self.gateway_server:
                try:
                    self.gateway_server.stop()
                except Exception:
                    pass
            if self.gateway_thread and self.gateway_thread.is_alive():
                self.gateway_thread.join(timeout=2.0)

            if self.desktop_manager:
                try:
                    self.desktop_manager.close_all_windows(timeout=1.5)
                except Exception:
                    pass
                try:
                    self.desktop_manager.terminate_tracked_processes()
                except Exception:
                    pass
                try:
                    self.desktop_manager.close()
                except Exception:
                    pass

            try:
                self.main_window.attach_agent_preview_source(None)
            except Exception:
                pass
        except Exception:
            pass

    @Slot(object)
    def handle_mode_changed(self, mode):
        self.gui_adapter.current_mode = mode
        self.gui_adapter.set_operation_mode(mode)
        if not self.agent:
            return
        try:
            self.agent.set_mode(mode)
            if self.live_session:
                self.live_session.notify_mode_changed(mode)
            if mode == OperationMode.GUIDE and self.agent.active_workspace != "user":
                self.agent._set_workspace(
                    "user",
                    reason="Guidance mode requires the user workspace",
                )
            self._live_action_passthrough_active = False
            self._apply_click_through_policy()
            self._sync_window_from_agent()
        except Exception as exc:
            self.gui_adapter.add_error_message(f"Failed to change mode: {exc}")

    @Slot(str)
    def handle_workspace_changed(self, workspace: str):
        if not self.agent:
            return

        workspace = self._normalize_workspace(workspace)
        try:
            self.agent.active_workspace = workspace
        except Exception:
            pass

        self.gui_adapter.set_workspace(workspace)
        self.gui_adapter.set_agent_view_enabled(workspace == "agent")

        if self.live_session:
            try:
                self.live_session.notify_workspace_changed(workspace)
            except Exception:
                pass

        self._apply_click_through_policy()
        self.update_sidecar_visibility()

    @Slot(str)
    def handle_vision_changed(self, vision_mode: str):
        mode_key = (vision_mode or "").strip().lower()
        self._apply_vision_flags(mode_key)
        self.gui_adapter.set_vision_mode("ROBO" if mode_key == "robo" else "OCR")

        if not self.agent:
            return

        if mode_key == "robo":
            try:
                self.agent.robotics_eye = self._create_robotics_eye()
                self.gui_adapter.set_vision_mode("ROBO")
                self.gui_adapter.add_system_message("Vision changed to ROBO")
            except Exception as exc:
                Config.USE_ROBOTICS_EYE = False
                Config.LAZY_VISION = True
                self.agent.robotics_eye = None
                self.gui_adapter.set_vision_mode("OCR")
                self.gui_adapter.add_error_message(
                    f"Failed to enable ROBO vision (using OCR): {exc}"
                )
        else:
            self.agent.robotics_eye = None
            self.gui_adapter.set_vision_mode("OCR")
            self.gui_adapter.add_system_message("Vision changed to OCR")

    def update_sidecar_visibility(self):
        workspace = "user"
        if self.agent:
            workspace = (self.agent.active_workspace or "user").strip().lower()
        is_agent_workspace = workspace == "agent"

        self.gui_adapter.set_agent_view_enabled(is_agent_workspace)

        if not is_agent_workspace:
            self.main_window.attach_agent_preview_source(None)
            self.gui_adapter.set_sidecar_visible(False)
            return

        if not self.desktop_manager or not getattr(self.desktop_manager, "is_created", False):
            self.init_sidecar()

        source = (
            self.desktop_manager
            if self.desktop_manager and getattr(self.desktop_manager, "is_created", False)
            else None
        )
        self.main_window.attach_agent_preview_source(source)
        self.main_window.refresh_agent_preview_visibility()

    @Slot(bool)
    def handle_live_mode_changed(self, enabled: bool):
        if not self.live_session:
            self.live_mode_enabled = False
            self.gui_adapter.set_live_enabled(False)
            self.gui_adapter.add_error_message(self._startup_message("Gemini Live", unavailable=True))
            return

        success = self.live_session.set_enabled(bool(enabled))
        self.live_mode_enabled = bool(enabled and success)
        if not self.live_mode_enabled:
            self._live_action_passthrough_active = False

        if self.live_mode_enabled and self.agent:
            try:
                self.live_session.notify_workspace_changed(self.agent.active_workspace)
            except Exception:
                pass

        self.gui_adapter.set_live_enabled(self.live_mode_enabled)
        self._apply_click_through_policy()

    @Slot(bool)
    def handle_live_voice_toggled(self, enabled: bool):
        if not self.live_session or not self.live_mode_enabled:
            return

        if enabled:
            if not self.live_session.start_voice():
                self._handle_live_voice_active(False)
        else:
            self.live_session.stop_voice()
            self._handle_live_voice_active(False)

    @Slot(str, str, bool)
    def _handle_live_transcript(self, speaker: str, text: str, final: bool):
        self.gui_adapter.update_live_transcript(speaker, text, final)

    @Slot(str)
    def _handle_live_session_state(self, state: str):
        self.gui_adapter.update_live_session_state(state)

    @Slot(object)
    def _handle_live_action_state(self, payload: object):
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").strip().lower()
            if status in {"queued", "running", "cancel_requested"}:
                self._live_action_passthrough_active = True
            elif status in {"succeeded", "failed", "cancelled"}:
                self._live_action_passthrough_active = False
            self._apply_click_through_policy()
            self.gui_adapter.update_live_action_state(payload)

    @Slot(str)
    def _handle_live_error(self, message: str):
        self.gui_adapter.add_error_message(message)

    @Slot(float)
    def _handle_live_audio_level(self, level: float):
        self.gui_adapter.update_live_audio_level(level)

    @Slot(float)
    def _handle_live_assistant_audio_level(self, level: float):
        self.gui_adapter.update_assistant_audio_level(level)

    @Slot(bool, str)
    def _handle_live_availability(self, available: bool, reason: str):
        if not available:
            self.live_mode_enabled = False
            self._live_action_passthrough_active = False
        self.gui_adapter.update_live_availability(available, reason)
        self._apply_click_through_policy()

    @Slot(bool)
    def _handle_live_voice_active(self, active: bool):
        self.gui_adapter.update_live_voice_active(active)
