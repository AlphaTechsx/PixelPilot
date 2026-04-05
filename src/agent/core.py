import ctypes
import importlib
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

from config import Config, OperationMode
from tools.app_index_service import AppIndexService


logger = logging.getLogger("pixelpilot.agent")


class StopRequested(Exception):
    pass


class AgentOrchestrator:
    """
    Shared live-runtime automation state for the desktop app.

    Gemini Live owns planning and conversation. This class keeps the execution
    surfaces, workspace state, capture pipeline, and desktop tools that the live
    session depends on.
    """

    _SKILL_FACTORIES = {
        "media": ("skills.media", "MediaSkill"),
        "browser": ("skills.browser", "BrowserSkill"),
        "system": ("skills.system", "SystemSkill"),
        "timer": ("skills.timer", "TimerSkill"),
    }

    def __init__(self, mode: OperationMode = None, chat_window=None, robotics_eye=None):
        self.mode = mode or Config.DEFAULT_MODE
        self.chat_window = chat_window
        self.robotics_eye = robotics_eye

        self._stop_event = threading.Event()
        self._keyboard = None
        self._action_executor = None
        self._screen_capture = None
        self._skill_instances: dict[str, Any] = {}
        self._app_index_service = AppIndexService(
            cache_path=Config.APP_INDEX_PATH,
            auto_refresh=Config.APP_INDEX_AUTO_REFRESH,
            include_processes=Config.APP_INDEX_INCLUDE_PROCESSES,
        )

        self.desktop_manager = None
        self.active_workspace = Config.DEFAULT_WORKSPACE

        self.current_task: Optional[str] = None
        self.task_history: list[dict[str, Any]] = []
        self.current_blind_snapshot: Optional[dict[str, Any]] = None
        self.deferred_reply: Optional[str] = None

        self.zoom_center = None
        self.zoom_level = 1.0
        self.zoom_offset = (0, 0)
        self.is_magnified = False

        os.makedirs(Config.MEDIA_DIR, exist_ok=True)
        self.log(f"AI agent initialized in {self.mode.value.upper()} mode")

    @property
    def action_executor(self):
        if self._action_executor is None:
            from agent.actions import ActionExecutor

            self._action_executor = ActionExecutor(self)
        return self._action_executor

    @property
    def screen_capture(self):
        if self._screen_capture is None:
            from agent.capture import ScreenCapture

            self._screen_capture = ScreenCapture(self)
        return self._screen_capture

    @property
    def keyboard(self):
        if self._keyboard is None:
            from tools.keyboard import KeyboardController

            self._keyboard = KeyboardController()
            if self.desktop_manager and hasattr(self._keyboard, "set_desktop_manager"):
                self._keyboard.set_desktop_manager(self.desktop_manager)
        return self._keyboard

    @property
    def app_indexer(self) -> AppIndexService:
        return self._app_index_service

    @property
    def browser_skill(self):
        return self.get_skill("browser")

    @property
    def media_skill(self):
        return self.get_skill("media")

    @property
    def system_skill(self):
        return self.get_skill("system")

    @property
    def timer_skill(self):
        return self.get_skill("timer")

    @property
    def skills(self) -> Dict[str, Any]:
        return {
            "media": self.media_skill,
            "spotify": self.media_skill,
            "browser": self.browser_skill,
            "system": self.system_skill,
            "timer": self.timer_skill,
        }

    def request_stop(self) -> None:
        self._stop_event.set()

    def clear_stop_request(self) -> None:
        self._stop_event.clear()

    def clear_session_context(self) -> None:
        self.clear_stop_request()
        self.current_task = None
        self.current_blind_snapshot = None
        self.deferred_reply = None
        self.task_history.clear()

    def set_mode(self, mode: OperationMode) -> None:
        self.mode = mode
        self.log(f"Mode changed to {mode.value.upper()}")

    def get_skill(self, skill_name: str):
        key = (skill_name or "").strip().lower()
        if key == "spotify":
            key = "media"
        if key not in self._SKILL_FACTORIES:
            return None
        if key in self._skill_instances:
            return self._skill_instances[key]

        module_name, class_name = self._SKILL_FACTORIES[key]
        module = importlib.import_module(module_name)
        skill_class = getattr(module, class_name)
        skill = skill_class()
        self._skill_instances[key] = skill
        return skill

    def start_app_index_warmup(self) -> bool:
        return self.app_indexer.start_warmup()

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise StopRequested()

    def execute_action(
        self,
        action: Dict[str, Any],
        elements: List[Dict],
    ) -> Dict[str, Any]:
        return self.action_executor.execute(action, elements)

    def _goal_terms(self) -> List[str]:
        words = re.findall(r"[a-zA-Z0-9]+", (self.current_task or "").lower())
        return list(dict.fromkeys(word for word in words if word))

    def capture_screen(self) -> Optional[str]:
        return self.screen_capture.capture_screen()

    def capture_and_detail(self, force_robotics: bool = False):
        return self.screen_capture.capture_and_detail(force_robotics)

    def _set_workspace(self, target: str, reason: Optional[str] = None) -> None:
        workspace = (target or "").strip().lower()
        if workspace not in {"user", "agent"}:
            return
        if self.mode == OperationMode.GUIDE and workspace == "agent":
            self.log("Guidance workspace policy enforced: staying on user workspace")
            workspace = "user"

        changed = self.active_workspace != workspace
        self.active_workspace = workspace

        if changed:
            if reason:
                self.log(f"Workspace set to {workspace}: {reason}")
            else:
                self.log(f"Workspace set to {workspace}")

        if self.chat_window and hasattr(self.chat_window, "notify_workspace_changed"):
            try:
                self.chat_window.notify_workspace_changed(workspace)
            except Exception:
                pass

    def _init_agent_desktop(self) -> bool:
        if not Config.ENABLE_AGENT_DESKTOP:
            return False
        if self.desktop_manager and self.desktop_manager.is_created:
            return True

        try:
            from desktop.desktop_manager import AgentDesktopManager

            self.desktop_manager = AgentDesktopManager(Config.AGENT_DESKTOP_NAME)
            if not self.desktop_manager.create_desktop():
                self.desktop_manager = None
                return False

            self.desktop_manager.initialize_shell()
            return True
        except Exception:
            self.desktop_manager = None
            return False

    def _ensure_workspace_active(self) -> None:
        if self.active_workspace != "agent":
            return
        if not self.desktop_manager or not self.desktop_manager.is_created:
            if not self._init_agent_desktop():
                self._set_workspace(
                    "user",
                    reason="Agent Desktop unavailable; continuing on user desktop",
                )

    def log(self, message: str) -> None:
        raw = "" if message is None else str(message)
        clean = raw.strip()
        if not clean:
            return

        is_trace = (
            raw.startswith(" ") or clean.startswith("[") or clean.startswith("->")
        )
        if is_trace:
            logger.debug(clean)
        else:
            logger.info(clean)

        if self.chat_window:
            if is_trace or clean.startswith("="):
                return
            self.chat_window.add_system_message(clean)
            return

        print(message)

    def get_scale_factor(self):
        try:
            import pyautogui

            user32 = ctypes.windll.user32
            if not self.chat_window:
                user32.SetProcessDPIAware()
            w_physical = user32.GetSystemMetrics(0)
            h_physical = user32.GetSystemMetrics(1)
            w_logical, h_logical = pyautogui.size()
            return w_logical / w_physical, h_logical / h_physical
        except Exception:
            return 1.0, 1.0
