import os
import pyautogui
import logging
import time
import threading
from typing import Any, Dict, List, Optional
from tools.app_indexer import AppIndexer
from agent.brain import create_reference_sheet, get_model, plan_task, get_client
from agent.clarification import ClarificationManager
from config import Config, OperationMode
from tools.eye import LocalCVEye
from tools.keyboard import KeyboardController
from tools.loop import LoopDetector
from skills import MediaSkill, BrowserSkill, SystemSkill, TimerSkill
from tools.mouse import click_at
from agent.actions import ActionExecutor
from agent.capture import ScreenCapture

class StopRequested(Exception):
    pass

class AgentOrchestrator:
    """
    Main AI Agent that orchestrates multi-step task execution.
    Implements vision + control loop with three operation modes.
    Handles UAC (Secure Desktop) transitions automatically.
    """

    def __init__(self, mode: OperationMode = None, chat_window=None, robotics_eye=None):
        """
        Initialize the AI Agent.

        Args:
            mode: Operation mode (GUIDE, SAFE, AUTO). Defaults to config default.
            chat_window: Optional ChatWindow instance for GUI mode
            robotics_eye: Optional instance of GeminiRoboticsEye
        """
        self.mode = mode or Config.DEFAULT_MODE
        self.robotics_eye = robotics_eye
        self.local_eye = LocalCVEye()
        self.keyboard = KeyboardController()
        self.task_history = []
        self.current_task = None
        self.step_count = 0
        self.max_steps = Config.MAX_TASK_STEPS
        self.chat_window = chat_window

        self._stop_event = threading.Event()

        if Config.ENABLE_LOOP_DETECTION:
            self.loop_detector = LoopDetector(
                threshold=Config.LOOP_DETECTION_THRESHOLD,
                similarity_threshold=Config.LOOP_SCREEN_SIMILARITY_THRESHOLD,
            )
        else:
            self.loop_detector = None

        self.app_indexer = AppIndexer(
            cache_path=Config.APP_INDEX_PATH,
            auto_refresh=False,
            include_processes=Config.APP_INDEX_INCLUDE_PROCESSES,
        )
        if Config.APP_INDEX_AUTO_REFRESH:
            threading.Thread(target=self.app_indexer.refresh, daemon=True).start()

        self.media_skill = MediaSkill()
        self.browser_skill = BrowserSkill()
        self.system_skill = SystemSkill()
        self.timer_skill = TimerSkill()

        self.skills = {
            "media": self.media_skill,
            "spotify": self.media_skill,
            "browser": self.browser_skill,
            "system": self.system_skill,
            "timer": self.timer_skill,
        }

        self.clarification_manager = (
            ClarificationManager(chat_window=chat_window, mode=self.mode)
            if Config.ENABLE_CLARIFICATION
            else None
        )

        self.zoom_center = None
        self.zoom_level = 1.0
        self.zoom_offset = (0, 0)
        self.is_magnified = False
        self.model_history = []
        self.visual_memory = {}
        os.makedirs(Config.MEDIA_DIR, exist_ok=True)

        self.desktop_manager = None
        self.active_workspace = Config.DEFAULT_WORKSPACE
        
        self.action_executor = ActionExecutor(self)
        self.screen_capture = ScreenCapture(self)

        self.log(f"AI Agent initialized in {self.mode.value.upper()} mode")

    def request_stop(self):
        self._stop_event.set()

    def _check_stop(self):
        if self._stop_event.is_set():
            raise StopRequested()

    def execute_action(self, action: Dict[str, Any], elements: List[Dict]) -> bool:
        """
        Execute a single action via ActionExecutor.
        """
        return self.action_executor.execute(action, elements)

    def run_task(self, user_command: str) -> bool:
        """
        Main control loop for executing a user task.
        """
        self.current_task = user_command
        self.task_history = []
        self.step_count = 0
        self._check_stop()

        if self.chat_window:
            try:
                self.chat_window.set_click_through(False)
            except Exception:
                pass

        needs_vision = True
        self.is_magnified = False
        self.zoom_center = None

        self.log(f"Starting task: {user_command}")

        while self.step_count < self.max_steps:
            self._check_stop()
            self.step_count += 1
            self.log(f"--- Step {self.step_count} ---")

            elements = []
            screenshot_path = None
            ref_sheet = None

            if self.step_count == 1:
                self.log("Step 1: Planning blind first step...")
                from agent.brain import plan_task_blind_first_step
                action_data = plan_task_blind_first_step(
                    user_command,
                    history=self.task_history,
                    current_workspace=self.active_workspace,
                    agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created)
                )
                needs_vision = False
            else:
                if needs_vision:
                    elements, ref_sheet = self.capture_screen()
                    screenshot_path = Config.SCREENSHOT_PATH
                    
                    if not elements and (not screenshot_path or not os.path.exists(screenshot_path)):
                        self.log("Vision capture failed. Retrying with force robotics...")
                        time.sleep(1)
                        elements, ref_sheet = self.capture_screen(force_robotics=True)
                        if not elements:
                            self.log("CRITICAL: Screen capture failed repeatedly. Cannot proceed with vision.")
                            return False
                    
                    current_hash = self.screen_capture.last_hash
                    if self.task_history and self.task_history[-1].get("action_type") == "wait":
                        if getattr(self, "_last_run_hash", None) == current_hash:
                            self.log("Screen unchanged after wait. Extending wait...")
                            time.sleep(1)
                            continue
                    self._last_run_hash = current_hash
                else:
                    self.log("Blind mode: skipping screen capture")

                self._check_stop()
                if needs_vision:
                    action_data = plan_task(
                        user_command,
                        elements,
                        screenshot_path,
                        Config.DEBUG_PATH,
                        ref_sheet,
                        history=self.task_history,
                        current_workspace=self.active_workspace,
                        agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created)
                    )
                else:
                    from agent.brain import plan_task_blind
                    action_data = plan_task_blind(
                        user_command,
                        history=self.task_history,
                        current_workspace=self.active_workspace,
                        agent_desktop_available=(self.desktop_manager is not None and self.desktop_manager.is_created)
                    )

            if not action_data:
                self.log("Brain failed to provide a plan.")
                return False

            action, model_part = action_data
            self.log(f"AI Reasoning: {action.get('reasoning', 'N/A')}")
            
            if action.get("action_type") == "switch_workspace":
                params = action.get("params", {})
                target = params.get("workspace")
                if target:
                    self._set_workspace(target, reason=action.get("reasoning"))
                    needs_vision = True 
                    
                    self.task_history.append({
                        "step": self.step_count,
                        "action_type": action.get("action_type"),
                        "params": action.get("params"),
                        "reasoning": action.get("reasoning"),
                        "success": True
                    })
                    continue
            
            if self.step_count == 1:
                self._set_workspace(self.active_workspace)

            self._check_stop()
            success = self.execute_action(action, elements)
            
            self.task_history.append({
                "step": self.step_count,
                "action_type": action.get("action_type"),
                "params": action.get("params"),
                "reasoning": action.get("reasoning"),
                "success": success
            })

            if action.get("task_complete"):
                if not needs_vision and not action.get("skip_verification"):
                    self.log("Task marked complete in blind mode. Requesting vision for final verification.")
                    needs_vision = True
                    continue
                
                self.log("Task marked as complete by AI.")
                return True

            needs_vision = action.get("needs_vision", True)
            
            if action.get("action_type") == "magnify":
                self.is_magnified = True
                params = action.get("params", {})
                eid = params.get("element_id")
                if eid is not None:
                    for el in elements:
                        if el["id"] == eid:
                            self.zoom_center = (el["x"], el["y"])
                            break
                self.zoom_level = params.get("zoom_level", 2.0)
            elif action.get("action_type") != "wait":
                 self.is_magnified = False

        self.log("Max steps reached. Task timed out.")
        return False
        
    def capture_screen(self, force_robotics: bool = False) -> tuple[List[Dict], Optional[Any]]:
        """
        Delegate capture to ScreenCapture module.
        """
        return self.screen_capture.capture_screen(force_robotics)

    def _set_workspace(self, target: str, reason: Optional[str] = None) -> None:
        target = (target or "").strip().lower()
        if target not in {"user", "agent"}:
            return

        changed = (self.active_workspace != target)
        self.active_workspace = target

        if changed:
            if reason:
                self.log(f"Workspace set to {target}: {reason}")
            else:
                self.log(f"Workspace set to {target}")

        if self.chat_window:
            try:
                if hasattr(self.chat_window, "notify_workspace_changed"):
                    self.chat_window.notify_workspace_changed(target)
                if target == "user":
                    self.chat_window.set_click_through(True)
                else:
                    self.chat_window.set_click_through(False)
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

    def _restore_default_workspace(self, reason: str) -> None:
        target = (Config.DEFAULT_WORKSPACE or "user").strip().lower()
        if target not in {"user", "agent"}:
            target = "user"

        self._set_workspace(target, reason=reason)
        self._ensure_workspace_active()

    def log(self, message: str):
        """Log message to file and (sparingly) to the GUI.

        Policy:
        - GUI shows only high-level, human-readable updates.
        - Detailed trace lines (indented / bracketed) go to the log file (DEBUG).
        """

        logger = logging.getLogger("pixelpilot.agent")
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
        """
        Fixes mouse drift caused by Windows Display Scaling.
        """
        import ctypes
        try:
            user32 = ctypes.windll.user32
            if not self.chat_window:
                user32.SetProcessDPIAware()
            w_physical = user32.GetSystemMetrics(0)
            h_physical = user32.GetSystemMetrics(1)
            w_logical, h_logical = pyautogui.size()
            return w_logical / w_physical, h_logical / h_physical
        except Exception:
            return 1.0, 1.0




