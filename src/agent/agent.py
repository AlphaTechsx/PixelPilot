import ctypes
import hashlib
import os
import logging
import shutil
import time
import threading
import cv2
import PIL.Image
import pyautogui
import json
import mss
import subprocess
import tools.mouse as mouse
from typing import Any, Dict, List, Optional
from tools.app_indexer import AppIndexer
from agent.brain import create_reference_sheet, get_model, plan_task, get_model, types
from agent.clarification import ClarificationManager
from config import Config, OperationMode
from tools.eye import LocalCVEye
from tools.keyboard import KeyboardController
from tools.loop import LoopDetector
from skills import MediaSkill, BrowserSkill, SystemSkill, TimerSkill
from agent.verify import verify_task_completion
from agent.brain import plan_task_blind
from agent.guidance import create_guidance_session
from pydantic import BaseModel, Field


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
            auto_refresh=Config.APP_INDEX_AUTO_REFRESH,
            include_processes=Config.APP_INDEX_INCLUDE_PROCESSES,
        )

        self.media_skill = MediaSkill()
        self.browser_skill = BrowserSkill()
        self.system_skill = SystemSkill()
        self.timer_skill = TimerSkill()

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

        self.log(f"AI Agent initialized in {self.mode.value.upper()} mode")

    def request_stop(self):
        self._stop_event.set()

    def _check_stop(self):
        if self._stop_event.is_set():
            raise StopRequested()

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

        is_trace = raw.startswith(" ") or clean.startswith("[") or clean.startswith("->")
        if is_trace:
            logger.debug(clean)
        else:
            logger.info(clean)

        if self.chat_window:
            # Avoid turning the chat into a debug console.
            if is_trace or clean.startswith("="):
                return
            self.chat_window.add_system_message(clean)
            return

        print(message)

    def get_scale_factor(self):
        """
        Fixes mouse drift caused by Windows Display Scaling.
        """
        try:
            user32 = ctypes.windll.user32
            # If a Qt GUI is active, let Qt manage DPI awareness.
            # Calling SetProcessDPIAware can conflict with Qt's DPI initialization.
            if not self.chat_window:
                user32.SetProcessDPIAware()
            w_physical = user32.GetSystemMetrics(0)
            h_physical = user32.GetSystemMetrics(1)
            w_logical, h_logical = pyautogui.size()
            return w_logical / w_physical, h_logical / h_physical
        except Exception:
            return 1.0, 1.0

    def _ask_uac_brain(self, image_path: str) -> str:
        """Ask the Brain specifically about a UAC prompt."""
        try:
            self.log("   [UAC] Asking AI for decision/verification...")

            class UACDecision(BaseModel):
                decision: str = Field(description="The decision: 'ALLOW' or 'DENY'")
                reasoning: str = Field(
                    description="Why this decision was made. Analyze the publisher and program name."
                )
                confidence: float = Field(description="0.0 to 1.0")

            prompt = (
                "You are a security assistant looking at a Windows User Account Control (UAC) prompt or Secure Desktop.\n"
                "Your job is to decide whether to allow this action.\n\n"
                "CRITERIA:\n"
                "1. Analyze the 'Program Name' and 'Verified Publisher'.\n"
                "2. If it is a known system tool (cmd.exe, taskmgr, mmc, setup.exe) or legitimate installer, say 'ALLOW'.\n"
                "3. If the publisher is 'Unknown', be cautious. If it looks suspicious, say 'DENY'.\n"
                "4. If the image is BLACK/BLANK (technical capture error), assume it is the user's intended action and say 'ALLOW'.\n"
                "5. If you are unsure, default to 'DENY'.\n\n"
                "Respond with JSON: { 'decision': 'ALLOW'|'DENY', 'reasoning': '...' }"
            )

            model = get_model()
            img = PIL.Image.open(image_path)

            response = model.generate_content(
                [prompt, img],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": UACDecision.model_json_schema(),
                },
            )

            try:
                result = json.loads(response.text)
                decision = result.get("decision", "DENY").upper()
                reasoning = result.get("reasoning", "No reasoning provided")
                print(f"   [UAC Reasoning] {reasoning}")

                if "ALLOW" in decision:
                    return "ALLOW"
                return "DENY"
            except Exception:
                text = response.text.upper()
                if "ALLOW" in text:
                    return "ALLOW"
                return "DENY"

        except Exception as e:
            print(f"[UAC] Brain Error: {e}")
            return "ALLOW"

    def _get_screen_hash(self, image_path: str) -> str:
        """Calculate a hash of the screenshot to detect changes."""
        try:
            with open(image_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _is_screen_changed(self, current_path: str, previous_hash: str) -> bool:
        """Check if the screen has changed significantly."""
        if not previous_hash:
            return True
        current_hash = self._get_screen_hash(current_path)
        return current_hash != previous_hash

    def _is_black_screen(self, image: PIL.Image.Image) -> bool:
        """Check if the image is mostly black (UAC secure desktop symptom)."""
        try:
            extrema = image.convert("L").getextrema()
            if extrema[1] < 20:
                return True
            return False
        except Exception:
            return False

    def _capture_raw_image(self) -> PIL.Image.Image:
        """
        Capture raw screen execution-style.
        Attempts MSS (DXGI/Fast) first.
        If that fails or produces artifacts, falls back to PyAutoGUI (GDI).
        Raises Exception if both fail (likely UAC).
        """
        try:
            if not self.chat_window:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    sct_img = sct.grab(monitor)
                    img = PIL.Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    return img
        except Exception:
            pass
        return pyautogui.screenshot()

    def capture_screen(self, force_robotics: bool = False) -> tuple[List[Dict], Optional[Any]]:
        """
        Capture and analyze the current screen.
        Implements Lazy Vision: OCR first, fallback to Robotics if ambiguous.
        """
        if self.chat_window:
            self.chat_window.prepare_for_screenshot()

        self._check_stop()

        self.log("Taking screenshot...")

        max_retries = 3
        capture_successful = False

        for attempt in range(max_retries):
            self._check_stop()
            try:
                if os.path.exists(Config.SCREENSHOT_PATH):
                    try:
                        os.remove(Config.SCREENSHOT_PATH)
                    except Exception:
                        pass

                time.sleep(0.1)

                full_img = self._capture_raw_image()

                self._check_stop()

                if self.is_magnified and self.zoom_center:
                    w, h = full_img.size

                    crop_w = int(w / self.zoom_level)
                    crop_h = int(h / self.zoom_level)

                    left = max(0, int(self.zoom_center[0] - crop_w // 2))
                    top = max(0, int(self.zoom_center[1] - crop_h // 2))
                    right = min(w, left + crop_w)
                    bottom = min(h, top + crop_h)

                    if right == w:
                        left = max(0, w - crop_w)
                    if bottom == h:
                        top = max(0, h - crop_h)

                    self.zoom_offset = (left, top)
                    zoom_crop = full_img.crop((left, top, right, bottom))

                    magnified_img = zoom_crop.resize((w, h), PIL.Image.Resampling.LANCZOS)
                    magnified_img.save(Config.SCREENSHOT_PATH)
                else:
                    full_img.save(Config.SCREENSHOT_PATH)
                    self.zoom_offset = (0, 0)

                if self._is_black_screen(full_img):
                    raise Exception("Screen is black (likely Secure Desktop/UAC)")

                time.sleep(Config.SCREENSHOT_DELAY)

                if (
                    os.path.exists(Config.SCREENSHOT_PATH)
                    and os.path.getsize(Config.SCREENSHOT_PATH) > 0
                ):
                    capture_successful = True
                    break

            except Exception as e:
                err_msg = str(e)
                print(f"   Screenshot attempt {attempt + 1} failed: {err_msg}")

                if (
                    "OpenInputDesktop failed" in err_msg
                    or "screen grab failed" in err_msg
                    or "Access is denied" in err_msg
                    or "Screen is black" in err_msg
                ):
                    print(
                        "   [UAC DETECTED] Standard screenshot failed. Initiating Orchestrator protocol..."
                    )

                    self._check_and_trigger_uac()

                    print("   [WAITING] allowing UAC Agent to run on Secure Desktop (5s)...")

                    uac_snap_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp", "uac_snapshot.bmp")
                    found_snapshot = False
                    for _ in range(10):
                        if os.path.exists(uac_snap_path):
                            found_snapshot = True
                            break
                        time.sleep(0.5)

                    if found_snapshot:
                        print("   [SUCCESS] Secure Desktop snapshot found!")
                        try:
                            self._check_stop()
                            decision = self._ask_uac_brain(uac_snap_path)
                            print(f"   [UAC DECISION] AI says: {decision}")

                            resp_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp", "uac_response.txt")
                            with open(resp_path, "w") as f:
                                f.write(decision)

                            time.sleep(2.0)

                            shutil.copy(uac_snap_path, Config.SCREENSHOT_PATH)
                            try:
                                os.remove(uac_snap_path)
                            except Exception:
                                pass

                            capture_successful = True
                            break
                        except Exception as copy_err:
                            print(f"   [ERROR] Failed during UAC handling: {copy_err}")
                    else:
                        print(
                            "   [WARNING] No UAC snapshot found. The Orchestrator may not have launched the agent."
                        )

                time.sleep(0.5)

        if self.chat_window:
            self.chat_window.restore_after_screenshot()

        if not capture_successful or not os.path.exists(Config.SCREENSHOT_PATH):
            print("   Error: Could not capture screen after multiple attempts.")
            return [], None

        elements = []
        vision_method = "None"

        if not Config.USE_ROBOTICS_EYE or Config.LAZY_VISION:
            print("Scanning UI elements with OCR + Edge Detection...")
            elements = self.local_eye.get_screen_elements(Config.SCREENSHOT_PATH)
            vision_method = "OCR+Edge"

        needs_robotics = force_robotics
        if Config.LAZY_VISION and not force_robotics:
            has_unknown_icons = any(el.get("label") == "unknown_icon" for el in elements)
            text_count = sum(1 for el in elements if el["type"] == "text")

            if (text_count < 3 and len(elements) < 8) or (has_unknown_icons and text_count < 2):
                print("   [LAZY] Results are ambiguous or sparse, falling back to Robotics...")
                needs_robotics = True

        if Config.USE_ROBOTICS_EYE and (needs_robotics or not Config.LAZY_VISION):
            print("Scanning UI elements with Gemini Robotics-ER...")
            task_context = self.current_task if self.current_task else None
            current_step = None
            if self.task_history:
                last_action = self.task_history[-1]
                current_step = f"{last_action['action_type']}: {last_action['reasoning']}"

            if self.robotics_eye:
                if Config.ROBOTICS_USE_BOUNDING_BOXES:
                    elements = self.robotics_eye.get_screen_elements_with_boxes(
                        Config.SCREENSHOT_PATH, max_elements=Config.ROBOTICS_MAX_ELEMENTS
                    )
                else:
                    elements = self.robotics_eye.get_screen_elements(
                        Config.SCREENSHOT_PATH,
                        max_elements=Config.ROBOTICS_MAX_ELEMENTS,
                        task_context=task_context,
                        current_step=current_step,
                    )
                vision_method = "Gemini Robotics-ER"
            else:
                print("   [WARNING] Robotics Eye requested but not initialized. Falling back to OCR.")
                if not elements:
                     elements = self.local_eye.get_screen_elements(Config.SCREENSHOT_PATH)
                     vision_method = "OCR+Edge (Fallback)"

        self._create_annotated_image(Config.SCREENSHOT_PATH, elements, Config.DEBUG_PATH)

        reference_sheet = None
        if Config.ENABLE_REFERENCE_SHEET:
            crops = self.local_eye.get_crops_for_context(Config.SCREENSHOT_PATH, elements)
            reference_sheet = create_reference_sheet(crops)
            if reference_sheet and Config.SAVE_SCREENSHOTS:
                reference_sheet.save(Config.REF_PATH)

        print(f"Found {len(elements)} UI elements ({vision_method})")
        return elements, reference_sheet

    def _create_annotated_image(self, original_path, elements, output_path):
        """Draw Green IDs on the screenshot for Gemini."""
        try:
            img = cv2.imread(original_path)
            if img is None:
                return

            for el in elements:
                x, y = int(el["x"]), int(el["y"])
                w = int(el.get("w", 20))
                h = int(el.get("h", 20))

                cv2.rectangle(
                    img, (x - w // 2, y - h // 2), (x + w // 2, y + h // 2), (0, 255, 0), 2
                )

                label = str(el["id"])
                cv2.rectangle(img, (x, y - 25), (x + 30, y), (0, 0, 0), -1)
                cv2.putText(img, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            cv2.imwrite(output_path, img)
        except ImportError:
            pass

    def execute_action(self, action: Dict[str, Any], elements: List[Dict]) -> bool:
        """
        Execute a single action.
        """
        action_type = action.get("action_type")
        params = action.get("params", {})

        print(f"\n Action: {action_type}")
        print(f"Reasoning: {action['reasoning']}")

        if Config.should_ask_confirmation(self.mode, action["reasoning"]):
            if self.mode == OperationMode.GUIDE:
                print(f"[GUIDE MODE] Suggestion: {action_type} with {params}")
                return False
            elif self.mode == OperationMode.SAFE or Config.is_dangerous_action(action["reasoning"]):
                if self.chat_window:
                    confirm = self.chat_window.ask_confirmation(
                        "Action Review",
                        f"Action: {action_type}\nParams: {params}\n\nReason: {action['reasoning']}\n\nExecute this?",
                    )
                else:
                    confirm_str = input(" Execute this action? (y/n): ").strip().lower()
                    confirm = confirm_str == "y"

                if not confirm:
                    print("Action cancelled by user")
                    return False

        try:
            if action_type == "click":
                return self._execute_click(params, elements)
            elif action_type == "type_text":
                return self._execute_type_text(params)
            elif action_type == "press_key":
                return self._execute_press_key(params)
            elif action_type == "key_combo":
                return self._execute_key_combo(params)
            elif action_type == "wait":
                return self._execute_wait(params)
            elif action_type == "search_web":
                return self._execute_search_web(params)
            elif action_type == "open_app":
                return self._execute_open_app(params)
            elif action_type == "magnify":
                return self._execute_magnify(params, elements)
            elif action_type == "reply":
                return self._execute_reply(params)
            elif action_type == "call_skill":
                return self._execute_skill(params)
            elif action_type == "sequence":
                print(
                    "   [WARNING] 'sequence' type passed to execute_action. This should be handled in run_task."
                )
                return False
            else:
                print(f"Unknown action type: {action_type}")
                return False
        except Exception as e:
            print(f"Error executing action: {e}")
            return False

    def _execute_skill(self, params: Dict) -> bool:
        skill = params.get("skill")
        method = params.get("method")
        args = params.get("args", {})

        print(f"Executing skill '{skill}' method '{method}'")

        if skill == "media" or skill == "spotify":
            if not self.media_skill.enabled:
                self.log("Media skill is disabled.")
                return False

            result = "Unknown method"
            if method == "play":
                result = self.media_skill.play(query=args.get("query"))
            elif method == "pause":
                result = self.media_skill.pause()
            elif method == "next":
                result = self.media_skill.next_track()
            elif method == "previous":
                result = self.media_skill.previous_track()
            elif method == "status":
                result = self.media_skill.get_status()

            self.log(f"Media Skill Result: {result}")
            return True

        elif skill == "browser":
            if not self.browser_skill.enabled:
                return False
            result = "Unknown method"
            if method == "open":
                result = self.browser_skill.open_url(args.get("url"))
            elif method == "search":
                result = self.browser_skill.search(args.get("query"))
            self.log(f"Browser Skill Result: {result}")
            return True

        elif skill == "system":
            if not self.system_skill.enabled:
                return False
            result = "Unknown method"
            if method == "volume":
                result = self.system_skill.set_volume(args.get("action"))
            elif method == "lock":
                result = self.system_skill.lock_screen()
            elif method == "minimize":
                result = self.system_skill.minimize_all()
            elif method == "settings":
                result = self.system_skill.open_settings(args.get("page"))
            self.log(f"System Skill Result: {result}")
            return True

        elif skill == "timer":
            if not self.timer_skill.enabled:
                return False
            result = "Unknown method"
            if method == "timer":
                result = self.timer_skill.open_timer()
            elif method == "alarm":
                result = self.timer_skill.open_alarm()
            elif method == "stopwatch":
                result = self.timer_skill.open_stopwatch()
            self.log(f"Timer Skill Result: {result}")
            return True

        else:
            self.log(f"Unknown skill: {skill}")
            return False

    def _execute_click(self, params: Dict, elements: List[Dict]) -> bool:
        element_id = params.get("element_id") or params.get("target_id")
        if element_id is None:
            print(f"Missing element_id in click params. Received: {params}")
            return False

        target = next((el for el in elements if el["id"] == element_id), None)
        if not target:
            print(f"Element ID {element_id} not found")
            return False

        if self.is_magnified:
            full_w, full_h = pyautogui.size()

            norm_x = target["x"] / full_w
            norm_y = target["y"] / full_h

            crop_w = full_w / self.zoom_level
            crop_h = full_h / self.zoom_level

            real_x = self.zoom_offset[0] + (norm_x * crop_w)
            real_y = self.zoom_offset[1] + (norm_y * crop_h)
        else:
            real_x = target["x"]
            real_y = target["y"]

        scale_x, scale_y = self.get_scale_factor()
        final_x = real_x * scale_x
        final_y = real_y * scale_y

        print(f"Clicking ID {element_id} ('{target['label']}') at ({final_x:.0f}, {final_y:.0f})")
        mouse.click_at(int(final_x), int(final_y))

        time.sleep(Config.WAIT_AFTER_CLICK)
        return True

    def _execute_type_text(self, params: Dict) -> bool:
        text = params.get("text")
        if not text:
            print("Missing text in type_text params")
            return False

        success = self.keyboard.type_text(text, interval=Config.TYPING_INTERVAL)
        time.sleep(Config.WAIT_AFTER_TYPE)
        return success

    def _execute_press_key(self, params: Dict) -> bool:
        key = params.get("key")
        if not key:
            print("Missing key in press_key params")
            return False

        success = self.keyboard.press_key(key)
        time.sleep(Config.WAIT_AFTER_KEY)
        return success

    def _execute_key_combo(self, params: Dict) -> bool:
        keys = params.get("keys", [])
        if not keys:
            print("Missing keys in key_combo params")
            return False

        success = self.keyboard.key_combo(*keys)
        time.sleep(Config.WAIT_AFTER_KEY)
        return success

    def _execute_wait(self, params: Dict) -> bool:
        seconds = params.get("seconds", 1)
        print(f"Waiting {seconds} seconds...")
        time.sleep(seconds)
        return True

    def _execute_search_web(self, params: Dict) -> bool:
        query = params.get("query")
        if not query:
            print("Missing query in search_web params")
            return False

        print(f"Searching web for: {query}")
        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

        self.keyboard.press_key("win")
        time.sleep(0.5)
        self.keyboard.type_text("chrome")
        time.sleep(0.5)
        self.keyboard.press_key("enter")
        time.sleep(2)
        self.keyboard.type_text(search_url)
        time.sleep(0.5)
        self.keyboard.press_key("enter")
        time.sleep(2)
        return True

    def _execute_open_app(self, params: Dict) -> bool:
        app_name = params.get("app_name")
        if not app_name:
            print("Missing app_name in open_app params")
            return False

        print(f"Opening application: {app_name}")

        app_results = self.app_indexer.find_app(app_name, max_results=1)

        if app_results:
            app_info = app_results[0]
            print(f"   Found via app index: {app_info['name']} (confidence: {app_info['score']}%)")

            launch_method, launch_command = self.app_indexer.get_launch_command(app_info)

            if (launch_method == "executable" or launch_method == "startfile") and launch_command:
                try:
                    if launch_method == "executable":
                        subprocess.Popen(launch_command)
                    else:
                        os.startfile(launch_command)

                    time.sleep(2)
                    print(f"   Launched ({launch_method}): {launch_command}")
                    return True
                except Exception as e:
                    print(f"   Failed to launch directly ({launch_method}): {e}")

        print("   Using Start Menu search...")
        self.keyboard.press_key("win")
        time.sleep(0.5)
        self.keyboard.type_text(app_name)
        time.sleep(1)
        self.keyboard.press_key("enter")
        time.sleep(2)
        return True

    def _execute_magnify(self, params: Dict, elements: List[Dict]) -> bool:
        element_id = params.get("element_id")
        zoom_level = float(params.get("zoom_level", 2.0))

        if element_id is not None:
            target = next((el for el in elements if el["id"] == element_id), None)
            if target:
                print(
                    f"   Focusing on ID {element_id} ('{target['label']}') with {zoom_level}x zoom"
                )

                if self.is_magnified:
                    full_w, full_h = pyautogui.size()
                    norm_x = target["x"] / full_w
                    norm_y = target["y"] / full_h
                    crop_w = full_w / self.zoom_level
                    crop_h = full_h / self.zoom_level
                    real_x = self.zoom_offset[0] + (norm_x * crop_w)
                    real_y = self.zoom_offset[1] + (norm_y * crop_h)
                    self.zoom_center = (real_x, real_y)
                else:
                    self.zoom_center = (target["x"], target["y"])

                self.zoom_level = zoom_level
                self.is_magnified = True
                return True

        print("   Resetting zoom to full screen")
        self.is_magnified = False
        self.zoom_level = 1.0
        self.zoom_center = None
        return True

    def _execute_reply(self, params: Dict) -> bool:
        """Reply to the user's question without taking action on screen."""
        text = params.get("text", "")
        print(f"   [REPLY]: {text}")
        return True

    def _process_action_result(
        self, action: Dict[str, Any], elements: List[Dict], user_command: str
    ) -> bool:
        """
        Executes the action (or sequence) and handles verification.
        Returns True if action was successful.
        Updates self.task_history and handles task_complete logic internally.
        """
        task_complete = action.get("task_complete", False)
        action_sequence = action.get("action_sequence")
        success = False
        deferred_execution = False

        if not action_sequence and task_complete and action.get("action_type") == "reply":
            print("   [INFO] Deferring reply execution until verification completes...")
            deferred_execution = True
            success = True
        elif action_sequence and (Config.TURBO_MODE or action.get("action_type") == "sequence"):
            print(f"\n EXECUTING SEQUENCE: {len(action_sequence)} actions")
            success = True
            for i, sub_action in enumerate(action_sequence):
                print(f"\n--- Sequence Step {i + 1}/{len(action_sequence)} ---")
                step_success = self.execute_action(sub_action, elements)
                if not step_success:
                    print(f" Sequence failed at step {i + 1}")
                    success = False
                    break
                self.task_history.append(sub_action)
        else:
            success = self.execute_action(action, elements)
            if success:
                self.task_history.append(action)

        if success:
            if not deferred_execution:
                print("Action completed")
            uac_suspect = False
            
            def check_uac(act):
                if act.get("action_type") == "key_combo":
                    keys = [str(k).lower() for k in act.get("params", {}).get("keys", [])]
                    if ("alt" in keys and "y" in keys) or \
                       ("ctrl" in keys and "shift" in keys and "enter" in keys):
                        return True
                return False

            if action_sequence:
                for sub in action_sequence:
                    if check_uac(sub):
                        uac_suspect = True
            elif check_uac(action):
                uac_suspect = True

            if uac_suspect:
                print("   [UAC CHECK] Action may have triggered UAC. Forcing screen check...")
                time.sleep(2.0)
                self.capture_screen()

            if action.get("action_type") in [
                "click",
                "type_text",
                "press_key",
                "key_combo",
                "open_app",
                "wait",
                "reply",
            ]:
                if self.is_magnified:
                    print("   [Reset] Action finished, returning to full screen")
                    self.is_magnified = False
                    self.zoom_level = 1.0
                    self.zoom_center = None

            if task_complete:
                if (
                    not Config.ENABLE_VERIFICATION
                    or action.get("skip_verification", False)
                    or action.get("no_verification", False)
                ):
                    print("\n [INFO] Skipping verification as requested by action plan.")
                    action["task_complete"] = True
                else:
                    try:
                        print("\n Verifying task completion...")
                        time.sleep(Config.VERIFICATION_DELAY)
                        verify_elements, verify_ref = self.capture_screen()

                        if not verify_elements:
                            print("Verification screenshot failed. Trusting task completion.")
                            action["task_complete"] = True
                        else:
                            verification = verify_task_completion(
                                user_command,
                                action.get("expected_result", ""),
                                verify_elements,
                                Config.SCREENSHOT_PATH,
                                Config.DEBUG_PATH,
                                verify_ref,
                                self.task_history,
                            )

                            if verification:
                                is_actually_complete = verification.get("is_complete", False)
                                confidence = verification.get("confidence", 0.0)
                                reasoning = verification.get("reasoning", "")

                                print("\n Verification Result:")
                                print(f"   Complete: {is_actually_complete}")
                                print(f"   Confidence: {confidence:.0%}")
                                print(f"   Reasoning: {reasoning}")

                                if (
                                    is_actually_complete
                                    and confidence >= Config.VERIFICATION_MIN_CONFIDENCE
                                ):
                                    print(f"   [SUCCESS] Verification passed (Confidence: {confidence:.0%})")
                                    action["task_complete"] = True
                                else:
                                    print("\n Task not verified as complete")
                                    action["task_complete"] = False
                                    action["verification_failed"] = True
                                    action["verification_reasoning"] = reasoning
                            else:
                                print("\n Verification returned no result")
                                print("   Trusting AI's assessment: task complete")
                                action["task_complete"] = True

                    except KeyboardInterrupt:
                        print("\n\n   Interrupted by user during verification")
                        raise
                    except Exception as e:
                        print(f"\n Verification error: {e}")
                        print("   Trusting AI's original assessment: task complete")
                        action["task_complete"] = True

                if action["task_complete"]:
                    print(f"\n TASK COMPLETED: {user_command}")

            if deferred_execution:
                if action.get("task_complete", False):
                    print("   [INFO] Verification passed. Executing deferred reply.")
                    real_success = self.execute_action(action, elements)
                    if real_success:
                        self.task_history.append(action)
                else:
                    print("   [INFO] Verification failed. Suppressing reply.")
                    self.task_history.append(action)

        else:
            print("Action failed or skipped")
            if self.mode == OperationMode.GUIDE:
                pass

        return success

    def run_task_guidance(self, user_command: str) -> bool:
        """
        Interactive Guidance Mode: step-by-step tutorial with conversational interaction.
        
        The AI watches the screen and provides instructions while the user
        performs actions themselves. Supports clarification questions mid-step.
        """
        self.log(f"\n{'=' * 60}")
        self.log(f"GUIDANCE MODE: {user_command}")
        self.log(f"{'=' * 60}")

        if self.chat_window:
            self.chat_window.set_click_through(False)

        try:
            self._stop_event.clear()
            self.current_task = user_command
            
            # Create and run the guidance session
            session = create_guidance_session(
                user_goal=user_command,
                chat_window=self.chat_window,
                capture_func=self.capture_screen,
                stop_check_func=self._check_stop,
            )
            
            return session.run()
            
        except StopRequested:
            self.log("Guidance session stopped by user")
            return False
            
        except Exception as e:
            self.log(f"Guidance session error: {e}")
            return False
            
        finally:
            if self.chat_window:
                self.chat_window.set_click_through(False)

    def _record_guidance_feedback(self, feedback: str) -> bool:
        """Legacy method - kept for compatibility."""
        return False


    def run_task(self, user_command: str) -> bool:
        """
        Execute a complete task with multi-step planning and execution.
        """
        if self.mode == OperationMode.GUIDE:
            return self.run_task_guidance(user_command)
        self.log(f"\n{'=' * 60}")
        self.log(f"NEW TASK: {user_command}")
        self.log(f"{'=' * 60}")

        if self.chat_window:
            self.chat_window.set_click_through(True)

        if self.loop_detector:
            self.loop_detector.clear()

        try:
            self._stop_event.clear()
            self.current_task = user_command
            self.step_count = 0
            self.task_history = []
            self.model_history = []

            task_complete = False
            max_retries = Config.MAX_RETRIES
            last_screen_hash = None
            cached_elements = []
            cached_ref = None
            reflexion_context = ""

            vision_active = not Config.ENABLE_BLIND_MODE

            while not task_complete and self.step_count < self.max_steps:
                self._check_stop()
                self.step_count += 1
                self.log(f"\n--- Step {self.step_count}/{self.max_steps} ---")

                if not vision_active:
                    self.log("Planning action (Blind Mode)...")

                    self._check_stop()

                    task_ctx = (
                        "\n".join(
                            [
                                f"Step {i + 1}: {step['action_type']} - {step['reasoning']}"
                                + (
                                    f" [VERIFICATION FAILED: {step['verification_reasoning']}]"
                                    if step.get("verification_failed")
                                    else ""
                                )
                                for i, step in enumerate(self.task_history)
                            ]
                        )
                        if self.task_history
                        else ""
                    )
                    plan_result = plan_task_blind(user_command, task_ctx, self.model_history)

                    if plan_result:
                        action, model_response = plan_result

                        self._check_stop()

                        if action.get("needs_vision", False):
                            print(f"   [BLIND] Vision requested: {action.get('reasoning')}")
                            print("   [MODE SWITCH] Activating Vision System...")
                            vision_active = True
                            self.step_count -= 1
                            continue

                        self.model_history.append(model_response)

                        success = self._process_action_result(action, [], user_command)
                        if success and action.get("task_complete", False):
                            task_complete = True

                        if not task_complete:
                            time.sleep(0.5)

                        continue

                    else:
                        print("   [BLIND] Planning failed. Switching to Vision.")
                        vision_active = True
                        self.step_count -= 1
                        continue

                use_cache = False
                if Config.INCREMENTAL_SCREENSHOTS and last_screen_hash and self.task_history:
                    last_action = self.task_history[-1]

                    if last_action["action_type"] in ["type_text", "wait"]:
                        temp_path = Config.TEMP_SCREEN_PATH
                        pyautogui.screenshot(temp_path)
                        if not self._is_screen_changed(temp_path, last_screen_hash):
                            self.log("   [INCREMENTAL] Screen unchanged, using cached elements.")
                            use_cache = True
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass

                if use_cache:
                    elements, reference_sheet = cached_elements, cached_ref
                else:
                    self._check_stop()
                    elements, reference_sheet = self.capture_screen()
                    last_screen_hash = self._get_screen_hash(Config.SCREENSHOT_PATH)
                    cached_elements, cached_ref = elements, reference_sheet

                screen_hash = (
                    self.loop_detector.hash_screen(Config.SCREENSHOT_PATH)
                    if self.loop_detector
                    else ""
                )

                if not elements and not reference_sheet:
                    self.log("Skipping step due to capture failure.")
                    time.sleep(2)
                    continue

                task_context = (
                    "\n".join(
                        [
                            f"Step {i + 1}: {step['action_type']} - {step['reasoning']}"
                            + (
                                f" [VERIFICATION FAILED: {step['verification_reasoning']}]"
                                if step.get("verification_failed")
                                else ""
                            )
                            for i, step in enumerate(self.task_history)
                        ]
                    )
                    if self.task_history
                    else ""
                )

                if reflexion_context:
                    task_context += f"\n\n[REFLEXION LOG]:\n{reflexion_context}"

                mag_hint = (
                    f"ZOOMED IN (x{self.zoom_level}) at {self.zoom_center}. IDs are on the magnified view."
                    if self.is_magnified
                    else None
                )

                media_res = "low"
                if self.is_magnified:
                    media_res = "high"
                    self.log("   [Smart Res] Magnified view active. Switching to HIGH resolution.")
                elif self.task_history and self.task_history[-1].get("confidence", 1.0) < 0.8:
                    media_res = "high"
                    self.log(
                        "   [Smart Res] Low confidence in previous step. Switching to HIGH resolution."
                    )

                self.log(f"Planning next action (res: {media_res})...")
                plan_result = plan_task(
                    user_command,
                    elements,
                    Config.SCREENSHOT_PATH,
                    Config.DEBUG_PATH,
                    reference_sheet,
                    task_context,
                    mag_hint,
                    self.model_history,
                    media_resolution=media_res,
                )

                if not plan_result:
                    print("Failed to get action plan from AI")
                    max_retries -= 1
                    if max_retries <= 0:
                        print("Max retries exceeded")
                        return False
                    continue

                action, model_response = plan_result

                if not action.get("needs_vision", True):
                    print(f"   [VISION] Agent requested Blind Mode: {action.get('reasoning')}")
                    print("   [MODE SWITCH] Switching to Blind Mode (No Screenshots)...")
                    vision_active = False

                self.model_history.append(model_response)

                if self.loop_detector and self.loop_detector.track_action(action, screen_hash):
                    loop_info = self.loop_detector.get_loop_info()
                    print(f"\n LOOP DETECTED: {loop_info.get('pattern', 'unknown pattern')}")

                    suggestions = self.loop_detector.suggest_alternatives(user_command, action)

                    if self.clarification_manager:
                        user_response = self.clarification_manager.handle_loop_clarification(
                            loop_info, user_command, suggestions
                        )

                        if user_response is None:
                            print("User cancelled task due to loop")
                            return False

                        print(f"\nUser guidance: {user_response}")

                        self.loop_detector.clear()
                        time.sleep(1)
                        continue
                    else:
                        print("Suggestions to break loop:")
                        for i, sug in enumerate(suggestions, 1):
                            print(f"  {i}. {sug}")
                        time.sleep(2)
                        self.loop_detector.clear()

                if (
                    self.clarification_manager
                    and self.clarification_manager.should_ask_clarification(action)
                ):
                    print(
                        f"\n AI confidence is low ({action.get('confidence', 0.0):.0%}) - asking for clarification..."
                    )

                    answer = self.clarification_manager.ask_question(action, user_command)

                    if answer is None:
                        print("User cancelled clarification")
                        return False

                    action = self.clarification_manager.integrate_answer(
                        action, answer, user_command
                    )

                    if not action:
                        print("Failed to process user's answer")
                        continue

                task_complete = action.get("task_complete", False)
                success = self._process_action_result(action, elements, user_command)
                task_complete = action.get("task_complete", False)

                if not success:
                    if self.mode == OperationMode.GUIDE:
                        task_complete = True

                if not task_complete:
                    time.sleep(1)

            if self.step_count >= self.max_steps:
                print(f"\n Task reached max steps ({self.max_steps})")
                return False

            return task_complete

        except StopRequested:
            return False

        finally:
            if self.chat_window:
                self.chat_window.set_click_through(False)

    def _check_and_trigger_uac(self):
        """Check if we are locked out by UAC and trigger the orchestrator."""
        try:
            print("   >>> TRIGGERING UAC ORCHESTRATOR <<<")
            trigger_path = Config.UAC_TRIGGER_PATH
            with open(trigger_path, "w") as f:
                f.write("trigger")

        except Exception as e:
            print(f"   Error triggering UAC: {e}")

    def set_mode(self, mode: OperationMode):
        """Change the operation mode."""
        self.mode = mode
        print(f"Mode changed to: {mode.value.upper()}")


if __name__ == "__main__":
    agent = AgentOrchestrator(OperationMode.SAFE)
    agent.run_task("Open Notepad")
