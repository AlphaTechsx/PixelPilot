import sys
import threading
import logging
from PySide6.QtCore import QObject, Slot, QThread, Signal, Qt, QTimer, QCoreApplication
from PySide6.QtWidgets import QMessageBox, QInputDialog
from agent.agent import AgentOrchestrator
from config import Config, OperationMode
from tools.eye import GeminiRoboticsEye

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

        # Connect GUI Adapter signals to Main Window actions (blocking support)
        self.gui_adapter.confirmation_requested.connect(self.handle_confirmation)
        self.gui_adapter.input_requested.connect(self.handle_input)
        self.gui_adapter.screenshot_prep_requested.connect(self.handle_screenshot_prep)
        self.gui_adapter.screenshot_restore_requested.connect(self.handle_screenshot_restore)
        self.gui_adapter.click_through_requested.connect(self.handle_click_through)
        self.gui_adapter.guidance_next_requested.connect(self.handle_guidance_next)
        self.gui_adapter.guidance_input_requested.connect(self.handle_guidance_input)

    def init_agent(self):
        # Initialize Agent
        # Pass gui_adapter as chat_window
        try:
            robotics_eye = None
            if Config.USE_ROBOTICS_EYE:
                try:
                    robotics_eye = GeminiRoboticsEye(api_key=Config.GEMINI_API_KEY)
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
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to initialize agent: {e}")

    @Slot(str, str, object)
    def handle_confirmation(self, title, text, payload):
        res = QMessageBox.question(self.main_window, title, text, QMessageBox.Yes | QMessageBox.No)
        payload['result'] = (res == QMessageBox.Yes)
        payload['event'].set()

    @Slot(str, str, object)
    def handle_input(self, title, question, payload):
        text, ok = QInputDialog.getText(self.main_window, title, question)
        if ok:
            payload['result'] = text
        else:
            payload['result'] = None
        payload['event'].set()

    @Slot(object)
    def handle_screenshot_prep(self, payload):
        self.main_window.hide()
        QCoreApplication.processEvents() # Process events to ensure hide happens
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

        self.gui_adapter.add_activity_message(f"Executing: {text}")
        
        if self.worker and self.worker.isRunning():
            self.gui_adapter.add_error_message("Agent is busy.")
            return

        self.worker = AgentWorker(self.agent, text)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.start()

    def stop_current_request(self):
        """Attempt to stop the currently running agent task."""

        if not self.worker or not self.worker.isRunning():
            self.gui_adapter.add_activity_message("Nothing to stop")
            return

        self._stop_requested = True
        self.gui_adapter.add_activity_message("Stopping…")

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
            self.gui_adapter.add_system_message("Stopped")
            return
        if success:
            self.gui_adapter.add_activity_message("Done")
        else:
            self.gui_adapter.add_error_message("Task failed or incomplete")

    def toggle_click_through(self):
        """Toggle whether the overlay is interactive (receives input) or click-through."""

        try:
            current = bool(getattr(self.main_window, "click_through_enabled", False))
            self.main_window.set_click_through_enabled(not current)
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to toggle interactivity: {e}")

    @Slot(object)
    def handle_mode_changed(self, mode):
        if not self.agent:
            return
        try:
            self.agent.set_mode(mode)
            self.gui_adapter.current_mode = mode
            # Keep UI clean: do not narrate internal mode changes in chat.
            self.gui_adapter.add_activity_message("Settings updated")
        except Exception as e:
            self.gui_adapter.add_error_message(f"Failed to change mode: {e}")

    @Slot(str)
    def handle_vision_changed(self, vision_mode: str):
        if not self.agent:
            return

        mode_key = (vision_mode or "").strip().lower()
        if mode_key == "robo":
            Config.USE_ROBOTICS_EYE = True
            Config.LAZY_VISION = False
            try:
                self.agent.robotics_eye = GeminiRoboticsEye(api_key=Config.GEMINI_API_KEY)
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
