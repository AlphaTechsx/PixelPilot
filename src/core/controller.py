import logging
from PySide6.QtCore import QObject, Slot, QThread, Signal, QCoreApplication
from PySide6.QtWidgets import QDialog
from agent.core import AgentOrchestrator
from config import Config, OperationMode
from tools.eye import GeminiRoboticsEye
from backend_client import RateLimitError
from ui.custom_dialogs import ClarificationDialog, ConfirmationDialog

logger = logging.getLogger("pixelpilot.controller")

class AgentWorker(QThread):
    finished = Signal(bool)
    
    def __init__(self, agent, command):
        super().__init__()
        self.agent = agent
        self.command = command
        
    def run(self):
        try:
            success = self.agent.run_task(self.command)
            self.finished.emit(success)
        except RateLimitError as e:
            logger.warning(f"Rate limit exceeded: {e}")
            self.agent.chat_window.add_error_message(str(e))
            self.finished.emit(False)
        except Exception as e:
            logger.exception("Agent execution error")
            self.finished.emit(False)

class MainController(QObject):
    def __init__(self, gui_adapter, main_window):
        super().__init__()
        self.gui_adapter = gui_adapter
        self.main_window = main_window
        self.agent = None
        self.worker = None
        self._stop_requested = False
        self.live_session = None
        self.live_mode_enabled = False
        self._live_action_passthrough_active = False
        self._task_passthrough_active = False
        
        self.desktop_manager = None

        self.gui_adapter.confirmation_requested.connect(self.handle_confirmation)
        self.gui_adapter.input_requested.connect(self.handle_input)
        self.gui_adapter.screenshot_prep_requested.connect(self.handle_screenshot_prep)
        self.gui_adapter.screenshot_restore_requested.connect(self.handle_screenshot_restore)
        self.gui_adapter.click_through_requested.connect(self.handle_click_through)
        self.gui_adapter.guidance_next_requested.connect(self.handle_guidance_next)
        self.gui_adapter.guidance_input_requested.connect(self.handle_guidance_input)
        self.gui_adapter.workspace_changed.connect(self.handle_workspace_changed)

    @staticmethod
    def _normalize_workspace(workspace: str) -> str:
        key = (workspace or "user").strip().lower() or "user"
        if key not in {"user", "agent"}:
            key = "user"
        return key

    def _resolve_current_workspace(self) -> str:
        if not self.agent:
            return "user"
        return self._normalize_workspace(getattr(self.agent, "active_workspace", "user"))

    def _resolve_current_mode(self) -> OperationMode:
        mode = None
        if self.agent:
            mode = getattr(self.agent, "mode", None)
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

    def _apply_click_through_policy(self):
        workspace = self._resolve_current_workspace()
        mode = self._resolve_current_mode()
        click_through = False

        if workspace == "user":
            if self._is_live_session_enabled():
                click_through = bool(self._live_action_passthrough_active)
            elif mode in {OperationMode.GUIDE, OperationMode.SAFE, OperationMode.AUTO}:
                click_through = bool(self._task_passthrough_active)

        try:
            self.main_window.set_click_through_enabled(click_through)
        except Exception:
            pass

    def _apply_default_live_mode(self):
        if not self.live_session:
            self.live_mode_enabled = False
            return

        available = bool(getattr(self.live_session, "is_available", False))
        should_enable = bool(Config.LIVE_MODE_DEFAULT_ENABLED and available)
        if should_enable:
            self.handle_live_mode_changed(True)
            return

        self.live_mode_enabled = False
        try:
            if self.main_window and hasattr(self.main_window, "chat_widget"):
                self.main_window.chat_widget.set_live_enabled(False)
        except Exception:
            pass

    def _force_user_workspace_for_mode_change(self):
        if not self.agent:
            return

        reason = "Mode change policy: switched to user workspace"
        setter = getattr(self.agent, "_set_workspace", None)
        if callable(setter):
            try:
                setter("user", reason=reason)
                return
            except Exception:
                pass

        try:
            self.agent.active_workspace = "user"
        except Exception:
            pass
        self.handle_workspace_changed("user")

    def init_agent(self):
        try:
            robotics_eye = None
            if Config.USE_ROBOTICS_EYE:
                try:
                    robotics_eye = GeminiRoboticsEye()
                except Exception as e:
                    Config.USE_ROBOTICS_EYE = False
                    Config.LAZY_VISION = True
                    self.gui_adapter.add_error_message(f"Robotics vision unavailable, falling back to OCR: {e}")

            self.agent = AgentOrchestrator(
                mode=Config.DEFAULT_MODE,
                chat_window=self.gui_adapter,
                robotics_eye=robotics_eye,
            )
            self.gui_adapter.current_mode = self.agent.mode

            try:
                if self.main_window and hasattr(self.main_window, "chat_widget"):
                    self.main_window.chat_widget.set_workspace_status(
                        self.agent.active_workspace
                    )
                    self.main_window.chat_widget.set_agent_view_enabled(
                        self.agent.active_workspace == "agent"
                    )
            except Exception:
                pass
            
            if self.desktop_manager:
                self.agent.desktop_manager = self.desktop_manager

            self._init_live_session()
            self._apply_default_live_mode()
            self._apply_click_through_policy()
            self.update_sidecar_visibility()
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to initialize agent: {e}")

    def _init_live_session(self):
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
            self.live_session.assistant_audio_level_changed.connect(self._handle_live_assistant_audio_level)
            self.live_session.availability_changed.connect(self._handle_live_availability)
            self.live_session.voice_active_changed.connect(self._handle_live_voice_active)

            available = bool(getattr(self.live_session, "is_available", False))
            reason = str(getattr(self.live_session, "unavailable_reason", "") or "")
            try:
                self.live_session.notify_mode_changed(self.agent.mode)
            except Exception:
                pass
            self._handle_live_availability(available, reason)
            self._handle_live_session_state("disconnected")
        except Exception as exc:
            logger.exception("Failed to initialize Gemini Live session")
            self.live_session = None
            self.live_mode_enabled = False
            self._handle_live_availability(False, str(exc))

    def init_sidecar(self):
        """Initialize Agent Desktop and bind capture source for sidecar preview."""
        if not Config.ENABLE_AGENT_DESKTOP:
            return
        
        try:
            from desktop.desktop_manager import AgentDesktopManager

            if self.desktop_manager and getattr(self.desktop_manager, "is_created", False):
                if self.main_window and hasattr(self.main_window, "ensure_sidecar"):
                    sidecar = self.main_window.ensure_sidecar()
                    sidecar.set_capture_source(self.desktop_manager)
                return

            self.desktop_manager = AgentDesktopManager(Config.AGENT_DESKTOP_NAME)
            if not self.desktop_manager.create_desktop():
                logger.warning("Failed to create Agent Desktop")
                self.desktop_manager = None
                if self.main_window and getattr(self.main_window, "sidecar", None):
                    self.main_window.sidecar.hide()
                return
            
            self.desktop_manager.initialize_shell()

            if self.main_window and hasattr(self.main_window, "ensure_sidecar"):
                sidecar = self.main_window.ensure_sidecar()
                sidecar.set_capture_source(self.desktop_manager)
            
            if self.agent:
                self.agent.desktop_manager = self.desktop_manager
                if hasattr(self.agent, 'keyboard') and hasattr(self.agent.keyboard, 'set_desktop_manager'):
                    self.agent.keyboard.set_desktop_manager(self.desktop_manager)
                
            logger.info("Agent Desktop initialized for sidecar preview")
            self.update_sidecar_visibility()
        except Exception as e:
            logger.exception(f"Failed to initialize Agent Desktop: {e}")
            self.desktop_manager = None
            if self.main_window and getattr(self.main_window, "sidecar", None):
                self.main_window.sidecar.hide()

    @Slot(str, str, object)
    def handle_confirmation(self, title, text, payload):
        dialog = ConfirmationDialog(self.main_window, title, text)
        result = dialog.exec()
        payload['result'] = (result == QDialog.DialogCode.Accepted)
        payload['event'].set()

    @Slot(str, str, object)
    def handle_input(self, title, question, payload):
        dialog = ClarificationDialog(self.main_window, title, question)
        if dialog.exec():
            payload['result'] = dialog.get_text()
        else:
            payload['result'] = None
        payload['event'].set()

    @Slot(object)
    def handle_screenshot_prep(self, payload):
        self.main_window.hide()
        QCoreApplication.processEvents() 
        payload['event'].set()

    @Slot(object)
    def handle_screenshot_restore(self, payload):
        self.main_window.show()
        payload['event'].set()

    @Slot(bool, object)
    def handle_click_through(self, enable, payload):
        try:
            self.main_window.set_click_through_enabled(bool(enable))
        except Exception:
            pass
        payload['event'].set()

    @Slot(str, object)
    def handle_guidance_next(self, label, payload):
        try:
            self.main_window.chat_widget.show_guidance_button(label, payload)
        except Exception:
            payload["result"] = False
            payload["event"].set()

    @Slot(object)
    def handle_guidance_input(self, payload):
        """Handle conversational guidance input request."""
        try:
            self.main_window.chat_widget.show_guidance_input(payload)
        except Exception:
            payload["cancelled"] = True
            payload["event"].set()

    def handle_user_command(self, text):
        if not self.agent:
            self.gui_adapter.add_error_message("Agent not initialized.")
            return

        if self.live_mode_enabled and self.live_session and self.live_session.enabled:
            if self.worker and self.worker.isRunning():
                self.gui_adapter.add_error_message(
                    "A standard task is still running. Stop it before sending Gemini Live input."
                )
                return
            if not self.live_session.submit_text(text):
                self.gui_adapter.add_error_message("Failed to send input to Gemini Live.")
            return

        self.gui_adapter.add_activity_message(f"Executing: {text}")
        self._task_passthrough_active = True
        self._apply_click_through_policy()
        
        self.update_sidecar_visibility()

        self.worker = AgentWorker(self.agent, text)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.start()

    def stop_current_request(self):
        """Attempt to stop the currently running agent task."""

        live_stopped = False
        if self.live_mode_enabled and self.live_session and self.live_session.enabled:
            self.gui_adapter.add_activity_message("Stopping...")
            self.live_session.request_stop()
            live_stopped = True

        if not self.worker or not self.worker.isRunning():
            if not live_stopped:
                self.gui_adapter.add_activity_message("Nothing to stop")
            return

        self._stop_requested = True
        if not live_stopped:
            self.gui_adapter.add_activity_message("Stopping...")

        try:
            if hasattr(self.agent, "request_stop"):
                self.agent.request_stop()
        except Exception:
            pass

        try:
            self.worker.requestInterruption()
        except Exception:
            pass

    def on_task_finished(self, success):
        if self._stop_requested:
            self._stop_requested = False
            self._task_passthrough_active = False
            self._apply_click_through_policy()
            self.gui_adapter.add_system_message("Stopped")
            return
        self._task_passthrough_active = False
        if success:
            self.gui_adapter.add_activity_message("Done")
        else:
            self.gui_adapter.add_error_message("Task failed or incomplete")
        
        self._apply_click_through_policy()
        self.update_sidecar_visibility()

    def toggle_click_through(self):
        """Toggle whether the overlay is interactive (receives input) or click-through."""

        try:
            current = bool(getattr(self.main_window, "click_through_enabled", False))
            self.main_window.set_click_through_enabled(not current)
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to toggle interactivity: {e}")

    def shutdown(self):
        """Gracefully stop running tasks and close agent desktop resources."""
        try:
            if self.worker and self.worker.isRunning():
                try:
                    self.stop_current_request()
                except Exception:
                    pass
                try:
                    self.worker.wait(2000)
                except Exception:
                    pass

            if self.live_session:
                try:
                    self.live_session.shutdown()
                except Exception:
                    pass

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
                if self.main_window and getattr(self.main_window, "sidecar", None):
                    self.main_window.sidecar.hide()
            except Exception:
                pass
        except Exception:
            pass

    @Slot(object)
    def handle_mode_changed(self, mode):
        if not self.agent:
            return
        try:
            self.agent.set_mode(mode)
            if self.live_session:
                try:
                    self.live_session.notify_mode_changed(mode)
                except Exception:
                    pass
            self.gui_adapter.current_mode = mode
            self._live_action_passthrough_active = False
            self._task_passthrough_active = False
            self._force_user_workspace_for_mode_change()
            self._apply_click_through_policy()
            self.gui_adapter.add_activity_message("Settings updated")
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to change mode: {e}")

    @Slot(str)
    def handle_workspace_changed(self, workspace: str):
        if not self.agent:
            return

        workspace = self._normalize_workspace(workspace)
        try:
            self.agent.active_workspace = workspace
        except Exception:
            pass

        try:
            if self.main_window and hasattr(self.main_window, "chat_widget"):
                self.main_window.chat_widget.set_workspace_status(workspace)
        except Exception:
            pass

        if workspace != "user":
            self._live_action_passthrough_active = False
            self._task_passthrough_active = False

        if self.live_session:
            try:
                self.live_session.notify_workspace_changed(workspace)
            except Exception:
                pass

        self._apply_click_through_policy()
        self.update_sidecar_visibility()

    @Slot(str)
    def handle_vision_changed(self, vision_mode: str):
        if not self.agent:
            return

        mode_key = (vision_mode or "").strip().lower()
        if mode_key == "robo":
            Config.USE_ROBOTICS_EYE = True
            Config.LAZY_VISION = False
            try:
                self.agent.robotics_eye = GeminiRoboticsEye()
                self.gui_adapter.add_activity_message("Settings updated")
            except Exception as e:
                Config.USE_ROBOTICS_EYE = False
                Config.LAZY_VISION = True
                self.agent.robotics_eye = None
                try:
                    self.main_window.chat_widget.set_vision_mode("OCR")
                except Exception:
                    pass
                self.gui_adapter.add_error_message(f"Failed to enable ROBO vision (using OCR): {e}")
        else:
            Config.USE_ROBOTICS_EYE = False
            Config.LAZY_VISION = True
            self.agent.robotics_eye = None
            self.gui_adapter.add_activity_message("Settings updated")

    def update_sidecar_visibility(self):
        """Sync sidecar agent preview availability from workspace state."""
        if not self.main_window or not hasattr(self.main_window, "chat_widget"):
            return

        workspace = "user"
        if self.agent:
            workspace = (self.agent.active_workspace or "user").strip().lower()
        is_agent_workspace = workspace == "agent"

        try:
            self.main_window.chat_widget.set_agent_view_enabled(is_agent_workspace)
        except Exception:
            pass

        if not is_agent_workspace:
            if getattr(self.main_window, "sidecar", None):
                self.main_window.sidecar.hide()
            return

        if not self.desktop_manager or not getattr(self.desktop_manager, "is_created", False):
            self.init_sidecar()

        try:
            source = self.desktop_manager if self.desktop_manager and getattr(self.desktop_manager, "is_created", False) else None
            sidecar = self.main_window.ensure_sidecar()
            if source:
                sidecar.set_capture_source(source)

            should_show = bool(source and self.main_window.chat_widget.should_show_agent_view())
            if should_show:
                sidecar.show()
                sidecar.reattach()
            else:
                sidecar.hide()
        except Exception:
            pass

    @Slot(bool)
    def handle_live_mode_changed(self, enabled: bool):
        if not self.live_session:
            self.gui_adapter.add_error_message("Gemini Live is unavailable.")
            return

        success = self.live_session.set_enabled(bool(enabled))
        self.live_mode_enabled = bool(enabled and success)
        self._task_passthrough_active = False
        if not self.live_mode_enabled:
            self._live_action_passthrough_active = False
        if self.live_mode_enabled and self.agent:
            try:
                self.live_session.notify_workspace_changed(self.agent.active_workspace)
            except Exception:
                pass
        try:
            if self.main_window and hasattr(self.main_window, "chat_widget"):
                self.main_window.chat_widget.set_live_enabled(self.live_mode_enabled)
        except Exception:
            pass
        self._apply_click_through_policy()

    @Slot(bool)
    def handle_live_voice_toggled(self, enabled: bool):
        if not self.live_session or not self.live_mode_enabled:
            return

        if enabled:
            if not self.live_session.start_voice():
                self.gui_adapter.add_error_message("Failed to start Gemini Live voice session.")
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
        try:
            if self.main_window and hasattr(self.main_window, "chat_widget"):
                self.main_window.chat_widget.set_live_availability(available, reason)
        except Exception:
            pass
        self._apply_click_through_policy()

    @Slot(bool)
    def _handle_live_voice_active(self, active: bool):
        self.gui_adapter.update_live_voice_active(active)
