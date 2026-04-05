import hashlib
import os
import time
import cv2
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import pyautogui
import mss
import logging
from typing import Any, Dict, List, Optional

from config import Config
from tools.eye import LocalCVEye

logger = logging.getLogger("pixelpilot.capture")


def _create_reference_sheet(crops):
    if not crops:
        return None

    cell_w, cell_h = 100, 100
    cols = 8
    rows = (len(crops) + cols - 1) // cols
    sheet = PIL.Image.new("RGB", (cols * cell_w, rows * cell_h), color=(30, 30, 30))
    draw = PIL.ImageDraw.Draw(sheet)

    try:
        font = PIL.ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = PIL.ImageFont.load_default()

    for index, item in enumerate(crops):
        image = item["image"]
        image.thumbnail((cell_w - 10, cell_h - 30))
        col, row = index % cols, index // cols
        x = col * cell_w + 5
        y = row * cell_h + 30
        sheet.paste(image, (x, y))
        draw.text((x, y - 25), f"ID:{item['id']}", fill=(0, 255, 0), font=font)

    return sheet

class ScreenCapture:
    """
    Handles screenshot capture and detailed visual analysis.
    """
    def __init__(self, agent_orchestrator):
        self.agent = agent_orchestrator
        self.local_eye = LocalCVEye()
    
    def log(self, message: str):
        self.agent.log(message)

    def progress(self, message: str):
        self.agent.log(message)
        chat_window = getattr(self.agent, "chat_window", None)
        if chat_window and hasattr(chat_window, "add_activity_message"):
            try:
                chat_window.add_activity_message(str(message))
            except Exception:
                pass

    @property
    def desktop_manager(self):
        if self.agent.active_workspace == "agent":
            return self.agent.desktop_manager
        return None

    @property
    def last_hash(self) -> str:
        return getattr(self, "_last_hash", "")

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

    def _capture_raw_image(self) -> PIL.Image.Image:
        """
        Capture raw screen execution-style.
        """
        if self.desktop_manager:
            try:
                img = self.desktop_manager.capture_desktop()
                if img is not None:
                    return img
            except Exception as e:
                logging.getLogger("pixelpilot.agent").debug(
                    f"Agent Desktop capture failed: {e}"
                )

        try:
            if not self.agent.chat_window:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    sct_img = sct.grab(monitor)
                    img = PIL.Image.frombytes(
                        "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX"
                    )
                    return img
        except Exception:
            pass
        return pyautogui.screenshot()


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
                    img,
                    (x - w // 2, y - h // 2),
                    (x + w // 2, y + h // 2),
                    (0, 255, 0),
                    2,
                )

                label = str(el["id"])
                cv2.rectangle(img, (x, y - 25), (x + 30, y), (0, 0, 0), -1)
                cv2.putText(
                    img,
                    label,
                    (x, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

            cv2.imwrite(output_path, img)
        except Exception as e:
            logger.debug(f"Could not create annotated image: {e}")

    def _safe_get_local_elements(
        self,
        screenshot_path: str,
        progress_callback=None,
    ) -> List[Dict]:
        """Run Eye extraction safely and return an empty list on failure."""
        try:
            return (
                self.local_eye.get_screen_elements(
                    screenshot_path, progress_callback=progress_callback
                )
                or []
            )
        except Exception as e:
            logger.error(f"Eye extraction failed: {e}")
            return []

    def _safe_get_robotics_elements(
        self, screenshot_path: str, task_context: Optional[str], current_step: Optional[str]
    ) -> List[Dict]:
        """Run Robotics-ER safely and return an empty list on failure."""
        if not self.agent.robotics_eye:
            return []
        try:
            if Config.ROBOTICS_USE_BOUNDING_BOXES:
                return self.agent.robotics_eye.get_screen_elements_with_boxes(
                    screenshot_path,
                    max_elements=Config.ROBOTICS_MAX_ELEMENTS,
                ) or []
            return self.agent.robotics_eye.get_screen_elements(
                screenshot_path,
                max_elements=Config.ROBOTICS_MAX_ELEMENTS,
                task_context=task_context,
                current_step=current_step,
            ) or []
        except Exception as e:
            logger.error(f"Robotics-ER extraction failed: {e}")
            return []

    def capture_screen(
        self, force_robotics: bool = False
    ) -> Optional[str]:
        """
        Capture only a screenshot of the current screen.

        Notes:
        - This function intentionally does not perform element/logo/edge analysis.
        - Use capture_and_detail for visual analysis overlays and IDs.
        """
        self.agent._ensure_workspace_active()

        if self.agent.chat_window and self.agent.active_workspace == "user":
            self.agent.chat_window.prepare_for_screenshot()

        self.agent._check_stop()
        self.progress("Taking screenshot...")

        max_retries = 3
        screenshot_path: Optional[str] = None

        for attempt in range(max_retries):
            self.agent._check_stop()
            try:
                if os.path.exists(Config.SCREENSHOT_PATH):
                    try:
                        os.remove(Config.SCREENSHOT_PATH)
                    except Exception:
                        pass

                time.sleep(0.1)

                full_img = self._capture_raw_image()

                self.agent._check_stop()

                if self.agent.is_magnified and self.agent.zoom_center:
                    w, h = full_img.size

                    crop_w = int(w / self.agent.zoom_level)
                    crop_h = int(h / self.agent.zoom_level)

                    left = max(0, int(self.agent.zoom_center[0] - crop_w // 2))
                    top = max(0, int(self.agent.zoom_center[1] - crop_h // 2))
                    right = min(w, left + crop_w)
                    bottom = min(h, top + crop_h)

                    if right == w:
                        left = max(0, w - crop_w)
                    if bottom == h:
                        top = max(0, h - crop_h)

                    self.agent.zoom_offset = (left, top)
                    zoom_crop = full_img.crop((left, top, right, bottom))

                    magnified_img = zoom_crop.resize(
                        (w, h), PIL.Image.Resampling.LANCZOS
                    )
                    magnified_img.save(Config.SCREENSHOT_PATH)
                else:
                    full_img.save(Config.SCREENSHOT_PATH)
                    self.agent.zoom_offset = (0, 0)

                self._last_hash = self._get_screen_hash(Config.SCREENSHOT_PATH)

                time.sleep(Config.SCREENSHOT_DELAY)

                if (
                    os.path.exists(Config.SCREENSHOT_PATH)
                    and os.path.getsize(Config.SCREENSHOT_PATH) > 0
                ):
                    screenshot_path = Config.SCREENSHOT_PATH
                    break

            except Exception as e:
                err_msg = str(e)
                logger.warning(f"Screenshot attempt {attempt + 1} failed: {err_msg}")
                time.sleep(0.1)

        if self.agent.chat_window and self.agent.active_workspace == "user":
            self.agent.chat_window.restore_after_screenshot()

        if not screenshot_path or not os.path.exists(screenshot_path):
            logger.error("Could not capture screen after multiple attempts.")
            return None

        self.progress("Screenshot captured.")
        return screenshot_path

    def _create_edge_overlay(self, screenshot_path: str, output_path: str) -> None:
        """Create a basic edge map overlay for detail captures."""
        try:
            image = cv2.imread(screenshot_path)
            if image is None:
                return
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, threshold1=80, threshold2=180)
            edge_rgb = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
            cv2.imwrite(output_path, edge_rgb)
        except Exception as e:
            logger.debug(f"Could not create edge overlay: {e}")

    def capture_and_detail(
        self, force_robotics: bool = False
    ) -> tuple[List[Dict], Optional[Any]]:
        """
        Capture a screenshot and then run detailed visual analysis:
        - Logo/icon-oriented element detection
        - Element IDs and debug overlay
        - Edge overlay map
        """
        screenshot_path = self.capture_screen(force_robotics=force_robotics)
        if not screenshot_path:
            return [], None

        elements = []
        vision_method = "None"

        if not Config.USE_ROBOTICS_EYE or Config.LAZY_VISION:
            elements = self._safe_get_local_elements(
                screenshot_path,
                progress_callback=self.progress,
            )
            vision_method = self.local_eye.current_vision_label()

        needs_robotics = force_robotics
        if Config.LAZY_VISION and not force_robotics:
            has_unknown_icons = any(
                el.get("label") == "unknown_icon" for el in elements
            )
            text_count = sum(1 for el in elements if el["type"] == "text")

            if (text_count < 1 and len(elements) < 2) or (
                has_unknown_icons and text_count < 1
            ):
                self.log(
                    "Lazy vision fallback to Robotics (sparse context)..."
                )
                needs_robotics = True

        if Config.USE_ROBOTICS_EYE and (needs_robotics or not Config.LAZY_VISION):
            self.log("Scanning UI elements with Gemini Robotics-ER...")
            task_context = self.agent.current_task if self.agent.current_task else None
            current_step = None
            if self.agent.task_history:
                last_action = next(
                    (
                        h
                        for h in reversed(self.agent.task_history)
                        if isinstance(h, dict) and "action_type" in h
                    ),
                    None,
                )
                if last_action:
                    current_step = (
                        f"{last_action['action_type']}: {last_action['reasoning']}"
                    )

            if self.agent.robotics_eye:
                robo_elements = self._safe_get_robotics_elements(
                    screenshot_path, task_context, current_step
                )
                if robo_elements:
                    elements = robo_elements
                    vision_method = "Gemini Robotics-ER"
                else:
                    logger.warning("Robotics-ER returned no usable elements. Falling back to OCR.")
                    if not elements:
                        elements = self._safe_get_local_elements(
                            screenshot_path,
                            progress_callback=self.progress,
                        )
                        vision_method = f"{self.local_eye.current_vision_label()} (Fallback)"
            else:
                logger.warning("Robotics Eye requested but not initialized. Falling back to OCR.")
                if not elements:
                    elements = self._safe_get_local_elements(
                        screenshot_path,
                        progress_callback=self.progress,
                    )
                    vision_method = f"{self.local_eye.current_vision_label()} (Fallback)"

        self._create_annotated_image(
            screenshot_path, elements, Config.DEBUG_PATH
        )
        self._create_edge_overlay(screenshot_path, Config.EDGE_PATH)

        reference_sheet = None
        if Config.ENABLE_REFERENCE_SHEET:
            try:
                crops = self.local_eye.get_crops_for_context(
                    screenshot_path, elements
                )
                reference_sheet = _create_reference_sheet(crops)
                if reference_sheet and Config.SAVE_SCREENSHOTS:
                    reference_sheet.save(Config.REF_PATH)
            except Exception as e:
                logger.error(f"Reference sheet creation failed: {e}")

        self.progress("Capture complete.")
        self.log(f"Found {len(elements)} UI elements ({vision_method})")
        return elements, reference_sheet
