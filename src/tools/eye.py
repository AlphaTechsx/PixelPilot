import concurrent.futures
import warnings
import json
import os
from PIL import Image
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import base64

from backend_client import get_client
from agent.prompts import ROBOTICS_EYE_DYNAMIC_PROMPT, ROBOTICS_EYE_GENERAL_PROMPT

warnings.filterwarnings(
    "ignore", message="'pin_memory' argument is set as true but no accelerator is found"
)

import logging
logger = logging.getLogger("pixelpilot.eye")


class LocalCVEye:
    """Local computer-vision based eye using EasyOCR + contour/icon detection."""

    def __init__(self, lang: str = "en", use_gpu: Optional[bool] = None):
        self.lang = lang
        self._use_gpu = use_gpu
        self._reader = None

    @property
    def reader(self):
        if self._reader is None:
            import easyocr
            import torch
            use_gpu = torch.cuda.is_available() if self._use_gpu is None else self._use_gpu
            logger.info(f"Initializing EasyOCR Reader (GPU={use_gpu})...")
            self._reader = easyocr.Reader([self.lang], gpu=use_gpu)
        return self._reader

    def _run_ocr(self, img):
        logger.info("Running OCR...")
        return self.reader.readtext(img)

    def _run_icon_detection(self, img, text_boxes):
        logger.info("Detecting Icons (Dual-Pass High Sensitivity)...")
        return self.find_mystery_icons_sensitive(img, text_boxes)

    def get_screen_elements(self, image_path: str) -> List[Dict[str, Any]]:
        """Scans the screen for Text (OCR) and high-sensitivity Icon candidates.

        Uses parallel processing for faster detection.
        """
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not open or find the image: {image_path}")

        elements: List[Dict[str, Any]] = []
        element_id = 0

        with concurrent.futures.ThreadPoolExecutor() as executor:
            ocr_future = executor.submit(self._run_ocr, img)
            icon_future = executor.submit(self.find_mystery_icons_sensitive, img, [])

            ocr_results = ocr_future.result()
            raw_icons = icon_future.result()

            text_boxes = []
            for bbox, text, prob in ocr_results:
                if prob > 0.3:
                    (tl, tr, br, bl) = bbox
                    x, y = int(tl[0]), int(tl[1])
                    w, h = int(br[0] - tl[0]), int(br[1] - tl[1])

                    text_boxes.append([x, y, w, h])
                    elements.append(
                        {
                            "id": element_id,
                            "type": "text",
                            "label": text,
                            "confidence": prob,
                            "x": x + w // 2,
                            "y": y + h // 2,
                            "w": w,
                            "h": h,
                        }
                    )
                    element_id += 1

            for icon in raw_icons:
                ix, iy, iw, ih = (
                    icon["x"] - icon["w"] // 2,
                    icon["y"] - icon["h"] // 2,
                    icon["w"],
                    icon["h"],
                )
                box_area = iw * ih
                is_text = False

                for tx, ty, tw, th in text_boxes:
                    inter_x1 = max(ix, tx)
                    inter_y1 = max(iy, ty)
                    inter_x2 = min(ix + iw, tx + tw)
                    inter_y2 = min(iy + ih, ty + th)

                    if inter_x2 > inter_x1 and inter_y2 > inter_y1:
                        intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
                        if intersection > 0.3 * box_area:
                            is_text = True
                            break

                if not is_text:
                    icon["id"] = element_id
                    elements.append(icon)
                    element_id += 1

        return elements

    def find_mystery_icons_sensitive(self, img, existing_text_boxes):
        """Combines Canny Edges + Adaptive Thresholding to find both
        outlined icons and filled blobs.
        """
        import cv2
        import numpy as np
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        candidates = []

        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
        )
        kernel_small = np.ones((2, 2), np.uint8)
        opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel_small, iterations=1)
        cnts_thresh, _ = cv2.findContours(
            opened, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )

        v = np.median(gray)
        sigma = 0.33
        lower = int(max(0, (1.0 - sigma) * v))
        upper = int(min(255, (1.0 + sigma) * v))
        edges = cv2.Canny(gray, lower, upper)
        edges = cv2.dilate(edges, kernel_small, iterations=1)
        cnts_edges, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        all_contours = list(cnts_thresh) + list(cnts_edges)

        raw_boxes = []

        for cnt in all_contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            aspect = w / float(h)

            if area < 150 or area > 50000:
                continue

            if aspect > 6 or aspect < 0.2:
                continue

            raw_boxes.append([x, y, w, h])

        clean_boxes = self.non_max_suppression(raw_boxes, overlapThresh=0.3)

        for x, y, w, h in clean_boxes:
            center_x = x + w // 2
            center_y = y + h // 2

            is_text = False
            box_area = w * h

            for tx, ty, tw, th in existing_text_boxes:
                ix = max(x, tx)
                iy = max(y, ty)
                iw = min(x + w, tx + tw) - ix
                ih = min(y + h, ty + th) - iy

                if iw > 0 and ih > 0:
                    intersection = iw * ih
                    if intersection > 0.3 * box_area:
                        is_text = True
                        break

            if not is_text:
                label = "unknown_icon"
                if 0.8 < aspect < 1.2:
                    if 200 < area < 1000:
                        label = "small_icon"
                    elif 1000 < area < 4000:
                        label = "medium_icon"
                elif aspect > 2:
                    label = "horizontal_element"
                elif aspect < 0.5:
                    label = "vertical_element"

                candidates.append(
                    {
                        "type": "icon_candidate",
                        "label": label,
                        "x": center_x,
                        "y": center_y,
                        "w": w,
                        "h": h,
                    }
                )

        return candidates

    @staticmethod
    def non_max_suppression(boxes, overlapThresh):
        """Standard NMS to remove overlapping bounding boxes."""
        import numpy as np
        if len(boxes) == 0:
            return []

        boxes = np.array(boxes)
        pick = []

        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 0] + boxes[:, 2]
        y2 = boxes[:, 1] + boxes[:, 3]
        area = (x2 - x1 + 1) * (y2 - y1 + 1)
        idxs = np.argsort(y2)

        while len(idxs) > 0:
            last = len(idxs) - 1
            i = idxs[last]
            pick.append(i)

            xx1 = np.maximum(x1[i], x1[idxs[:last]])
            yy1 = np.maximum(y1[i], y1[idxs[:last]])
            xx2 = np.minimum(x2[i], x2[idxs[:last]])
            yy2 = np.minimum(y2[i], y2[idxs[:last]])

            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)

            overlap = (w * h) / area[idxs[:last]]

            idxs = np.delete(idxs, np.concatenate(([last], np.where(overlap > overlapThresh)[0])))

        return boxes[pick].astype("int").tolist()

    def get_crops_for_context(
        self, image_path: str, elements: List[Dict[str, Any]], max_crops: int = 60
    ):
        full_img = Image.open(image_path)
        crops = []
        sorted_elements = sorted(elements, key=lambda k: (k["y"], k["x"]))

        targets = [
            el
            for el in sorted_elements
            if el["type"] in ["icon_candidate", "icon", "button", "link", "menu_item"]
        ]
        targets = targets[:max_crops]

        for el in targets:
            pad = 2
            x, y = int(el["x"]), int(el["y"])
            w = int(el.get("w", 40)) or 40
            h = int(el.get("h", 40)) or 40

            left = max(0, x - w // 2 - pad)
            top = max(0, y - h // 2 - pad)
            right = min(full_img.width, x + w // 2 + pad)
            bottom = min(full_img.height, y + h // 2 + pad)

            crop_img = full_img.crop((left, top, right, bottom))
            crops.append({"id": el["id"], "image": crop_img})

        return crops


class GeminiRoboticsEye:
    """Vision system using Gemini Robotics-ER 1.5 for UI element detection via backend."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-robotics-er-1.5-preview",
    ):
        self.model = model
        self.client = get_client()

    def get_screen_elements(
        self,
        image_path: str,
        max_elements: int = 50,
        element_types: Optional[List[str]] = None,
        task_context: Optional[str] = None,
        current_step: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        logger.info("Using Gemini Robotics-ER for element detection (via Backend)...")

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        img = Image.open(image_path)
        img_width, img_height = img.size

        class UIElement(BaseModel):
            point: List[int] = Field(
                description="[y, x] coordinates normalized to 0-1000"
            )
            label: str = Field(description="Description of the element")
            type: str = Field(description="Element type: button, icon, text_field, etc")
            confidence: float = Field(description="Confidence score 0.0-1.0")
            relevance: Optional[float] = Field(
                description="Relevance to task 0.0-1.0", default=0.0
            )

        class UIElementList(BaseModel):
            elements: List[UIElement]

        prompt = self._build_dynamic_prompt(
            task_context=task_context,
            current_step=current_step,
            element_types=element_types,
            max_elements=max_elements,
        )

        try:
            response_data = self.client.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "mime_type": self._get_mime_type(image_path),
                                "data": base64.b64encode(image_bytes).decode("utf-8"),
                            },
                            {"text": prompt},
                        ],
                    }
                ],
                config={
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                    "response_json_schema": UIElementList.model_json_schema(),
                },
            )

            response_text = response_data["text"]
            result = UIElementList.model_validate_json(response_text)
            elements_data = result.elements

            elements = []
            for idx, elem in enumerate(elements_data):
                point = elem.point
                if len(point) < 2:
                    continue

                y_norm, x_norm = point[0], point[1]
                x_pixel = int((x_norm / 1000.0) * img_width)
                y_pixel = int((y_norm / 1000.0) * img_height)

                elements.append(
                    {
                        "id": idx,
                        "type": elem.type,
                        "label": elem.label,
                        "confidence": elem.confidence,
                        "x": x_pixel,
                        "y": y_pixel,
                        "w": 40,
                        "h": 40,
                    }
                )

            logger.info(f"Found {len(elements)} elements using Gemini Robotics")
            return elements

        except Exception as e:
            logger.error(f"Error calling Backend API: {e}")
            return []

    def get_screen_elements_with_boxes(
        self, image_path: str, max_elements: int = 25
    ) -> List[Dict[str, Any]]:
        logger.info(
            "Using Gemini Robotics-ER for bounding box detection (via Backend)..."
        )

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        img = Image.open(image_path)
        img_width, img_height = img.size

        prompt = f"""
Return bounding boxes for all interactive UI elements in this screenshot.

Interactive elements include: buttons, text fields, icons, links, menus, checkboxes, radio buttons, dropdowns, tabs, etc.

Format as JSON array:
[
  {{
    "box_2d": [ymin, xmin, ymax, xmax],
    "label": "descriptive name",
    "type": "button|text_field|icon|link|menu|checkbox|radio_button|dropdown|tab|other"
  }},
  ...
]

Guidelines:
- Coordinates are normalized to 0-1000
- Values in box_2d must be integers
- Label should describe what the element is or contains
- Type should match the UI element type
- Limit to {max_elements} most prominent interactive elements
- Never return masks or code fencing

Return only the JSON array.
"""

        try:
            response_data = self.client.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "mime_type": self._get_mime_type(image_path),
                                "data": base64.b64encode(image_bytes).decode("utf-8"),
                            },
                            {"text": prompt},
                        ],
                    }
                ],
                config={"temperature": 0.3, "thinking_config": {"thinking_budget": 0}},
            )

            response_text = response_data["text"].strip()

            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = (
                    "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                )

            elements_data = json.loads(response_text)

            elements = []
            for idx, elem in enumerate(elements_data):
                box = elem.get("box_2d", [0, 0, 100, 100])

                ymin_norm, xmin_norm, ymax_norm, xmax_norm = box

                xmin = int((xmin_norm / 1000.0) * img_width)
                ymin = int((ymin_norm / 1000.0) * img_height)
                xmax = int((xmax_norm / 1000.0) * img_width)
                ymax = int((ymax_norm / 1000.0) * img_height)

                w = xmax - xmin
                h = ymax - ymin
                center_x = xmin + w // 2
                center_y = ymin + h // 2

                elements.append(
                    {
                        "id": idx,
                        "type": elem.get("type", "unknown"),
                        "label": elem.get("label", "unknown"),
                        "confidence": 0.9,
                        "x": center_x,
                        "y": center_y,
                        "w": w,
                        "h": h,
                    }
                )

            logger.info(f"Found {len(elements)} elements with bounding boxes")
            return elements

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Gemini response: {e}")
            return []
        except Exception as e:
            logger.error(f"Error calling Backend API: {e}")
            return []

    def find_specific_elements(
        self, image_path: str, queries: List[str]
    ) -> List[Dict[str, Any]]:
        logger.info(f"Searching for specific elements: {queries} (via Backend)")

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        img = Image.open(image_path)
        img_width, img_height = img.size

        prompt = f"""
Get all points matching the following UI elements: {", ".join(queries)}.

Return JSON format:
[
  {{
    "point": [y, x],
    "label": "element description"
  }},
  ...
]

Guidelines:
- Points are in [y, x] format normalized to 0-1000
- Label should match one of the requested elements
- If an element appears multiple times, include all instances
- If an element is not found, return an empty entry for it

Return only the JSON array, no code fencing.
"""

        try:
            response_data = self.client.generate_content(
                model=self.model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "mime_type": self._get_mime_type(image_path),
                                "data": base64.b64encode(image_bytes).decode("utf-8"),
                            },
                            {"text": prompt},
                        ],
                    }
                ],
                config={"temperature": 0.3, "thinking_config": {"thinking_budget": 0}},
            )

            response_text = response_data["text"].strip()

            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = (
                    "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
                )

            elements_data = json.loads(response_text)

            elements = []
            for idx, elem in enumerate(elements_data):
                point = elem.get("point", [0, 0])
                y_norm, x_norm = point[0], point[1]
                x_pixel = int((x_norm / 1000.0) * img_width)
                y_pixel = int((y_norm / 1000.0) * img_height)

                elements.append(
                    {
                        "id": idx,
                        "type": "specific_query",
                        "label": elem.get("label", "unknown"),
                        "confidence": 0.85,
                        "x": x_pixel,
                        "y": y_pixel,
                        "w": 0,
                        "h": 0,
                    }
                )

            logger.info(f"Found {len(elements)} matching elements")
            return elements

        except Exception as e:
            logger.error(f"Error: {e}")
            return []

    def _build_dynamic_prompt(
        self,
        task_context: Optional[str],
        current_step: Optional[str],
        element_types: Optional[List[str]],
        max_elements: int,
    ) -> str:
        focus_hints = []
        priority_types = []

        if task_context or current_step:
            context_text = (task_context or "") + " " + (current_step or "")
            context_lower = context_text.lower()

            if any(word in context_lower for word in ["open", "launch", "start", "run"]):
                priority_types = ["button", "icon", "link", "menu"]
                focus_hints.append(
                    "Prioritize application launchers, menu items, and clickable buttons"
                )
                focus_hints.append("Look for Start menu, taskbar icons, or desktop shortcuts")

            elif any(word in context_lower for word in ["type", "enter", "input", "write", "fill"]):
                priority_types = ["text_field", "textarea", "input"]
                focus_hints.append("Prioritize input fields, text boxes, and editable areas")
                focus_hints.append("Identify fields where text can be entered")

            elif any(word in context_lower for word in ["click", "press", "select", "choose"]):
                priority_types = ["button", "checkbox", "radio_button", "link"]
                focus_hints.append("Prioritize clickable buttons, links, and selection controls")

            elif any(word in context_lower for word in ["search", "find", "look for"]):
                priority_types = ["text_field", "button", "icon"]
                focus_hints.append(
                    "Prioritize search boxes, search buttons, and search-related icons"
                )
                focus_hints.append("Look for magnifying glass icons or 'Search' labels")

            elif any(
                word in context_lower for word in ["close", "exit", "quit", "minimize", "maximize"]
            ):
                priority_types = ["button", "icon"]
                focus_hints.append("Prioritize window control buttons (X, minimize, maximize)")
                focus_hints.append("Look for close buttons, typically in top-right corner")

            elif any(
                word in context_lower for word in ["menu", "navigate", "go to", "open settings"]
            ):
                priority_types = ["menu", "dropdown", "link", "tab"]
                focus_hints.append("Prioritize navigation elements, menus, and tabs")
                focus_hints.append("Look for menu bars, dropdown menus, and navigation links")

            elif any(
                word in context_lower for word in ["submit", "confirm", "ok", "apply", "save"]
            ):
                priority_types = ["button"]
                focus_hints.append("Prioritize action buttons like Submit, OK, Apply, or Save")
                focus_hints.append("Typically found at the bottom of dialogs or forms")

            elif any(word in context_lower for word in ["cancel", "back", "return", "undo"]):
                priority_types = ["button", "link"]
                focus_hints.append("Prioritize Cancel, Back, or Undo buttons")

        if element_types:
            priority_types = list(set(priority_types + element_types))

        if priority_types or focus_hints:
            type_list = ", ".join(priority_types) if priority_types else "all interactive elements"
            focus_hints_str = "\n".join([f"- {hint}" for hint in focus_hints])
            prompt = ROBOTICS_EYE_DYNAMIC_PROMPT.format(
                task_context=task_context or "General UI interaction",
                current_step=current_step or "Detecting interactive elements",
                focus_hints_str=focus_hints_str,
                type_list=type_list,
                max_elements=max_elements,
            )
        else:
            prompt = ROBOTICS_EYE_GENERAL_PROMPT.format(max_elements=max_elements)

        return prompt

    def _get_mime_type(self, image_path: str) -> str:
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }
        return mime_types.get(ext, "image/png")
