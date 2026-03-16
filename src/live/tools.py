from __future__ import annotations

import logging
import os
import tempfile
import time
import json
import asyncio
import urllib.request
from collections.abc import Callable
from typing import Any, Optional

import pyautogui
from PIL import Image

from config import Config
from live.broker import LiveActionBroker
import tools.mouse as mouse
from tools import ui_automation

logger = logging.getLogger("pixelpilot.live.tools")


class LiveToolRegistry:
    def __init__(
        self,
        *,
        agent,
        broker: LiveActionBroker,
        on_capture_ready: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        self.agent = agent
        self.broker = broker
        self.on_capture_ready = on_capture_ready
        self._guidance_mode = False
        self.last_snapshot_summary: Optional[dict[str, Any]] = None
        self.last_capture_summary: Optional[dict[str, Any]] = None

    def set_guidance_mode(self, enabled: bool) -> None:
        self._guidance_mode = bool(enabled)

    @property
    def declarations(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mouse_click",
                "description": (
                    "Click on the current workspace. Prefer ui_element_id when a stable UI Automation "
                    "target exists; otherwise use x/y absolute screen coordinates."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "x": {"type": "INTEGER"},
                        "y": {"type": "INTEGER"},
                        "button": {"type": "STRING", "description": "left, right, or middle"},
                        "clicks": {"type": "INTEGER"},
                        "ui_element_id": {"type": "STRING"},
                    },
                },
            },
            {
                "name": "keyboard_type_text",
                "description": "Type text into the focused app, optionally focusing a UI Automation element first.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {"type": "STRING"},
                        "ui_element_id": {"type": "STRING"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "keyboard_press_key",
                "description": "Press one key one or more times.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "key": {"type": "STRING"},
                        "presses": {"type": "INTEGER"},
                    },
                    "required": ["key"],
                },
            },
            {
                "name": "keyboard_key_combo",
                "description": "Press a keyboard shortcut.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "keys": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["keys"],
                },
            },
            {
                "name": "ui_get_snapshot",
                "description": "Get a compact UI Automation snapshot of the active workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "goal_terms": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                },
            },
            {
                "name": "ui_list_windows",
                "description": "List windows visible to UI Automation on the active workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "title_contains": {"type": "STRING"},
                        "process_name": {"type": "STRING"},
                        "visible_only": {"type": "BOOLEAN"},
                        "max_windows": {"type": "INTEGER"},
                    },
                },
            },
            {
                "name": "ui_focus_window",
                "description": "Focus a top-level window via UI Automation.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "window_id": {"type": "STRING"},
                        "title_contains": {"type": "STRING"},
                        "process_name": {"type": "STRING"},
                        "restore": {"type": "BOOLEAN"},
                        "maximize": {"type": "BOOLEAN"},
                    },
                },
            },
            {
                "name": "ui_read_text",
                "description": "Read text from the active window or a UI Automation element.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "target": {"type": "STRING"},
                        "ui_element_id": {"type": "STRING"},
                        "max_chars": {"type": "INTEGER"},
                        "use_ocr_fallback": {"type": "BOOLEAN"},
                        "force_ocr": {"type": "BOOLEAN"},
                    },
                },
            },
            {
                "name": "app_open",
                "description": "Open or focus a desktop application.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "app_name": {"type": "STRING"},
                    },
                    "required": ["app_name"],
                },
            },
            {
                "name": "workspace_switch",
                "description": "Switch between the user and agent workspaces.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "workspace": {"type": "STRING", "description": "user or agent"},
                    },
                    "required": ["workspace"],
                },
            },
            {
                "name": "capture_screen",
                "description": (
                    "Capture a high-resolution still from the active workspace for detailed visual reasoning. "
                    "Use this only when UI Automation or coarse live video is insufficient."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "overlay_draw_box",
                "description": (
                    "Draw a highlight box on the user's screen overlay. "
                    "Use this for explanation/teaching annotations. "
                    "When ui_element_id is provided, the runtime can tighten the rectangle for precision."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "x": {"type": "NUMBER"},
                        "y": {"type": "NUMBER"},
                        "width": {"type": "NUMBER"},
                        "height": {"type": "NUMBER"},
                        "x_min": {"type": "NUMBER"},
                        "y_min": {"type": "NUMBER"},
                        "x_max": {"type": "NUMBER"},
                        "y_max": {"type": "NUMBER"},
                        "ui_element_id": {"type": "STRING"},
                        "tight": {"type": "BOOLEAN"},
                        "inset_px": {"type": "INTEGER"},
                        "normalized": {"type": "BOOLEAN"},
                        "color": {"type": "STRING"},
                        "stroke_width": {"type": "NUMBER"},
                        "corner_radius": {"type": "NUMBER"},
                        "opacity": {"type": "NUMBER"},
                        "ttl_ms": {"type": "INTEGER"},
                    },
                },
            },
            {
                "name": "overlay_draw_text",
                "description": (
                    "Draw teaching/explanation text on the user's screen overlay."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "x": {"type": "NUMBER"},
                        "y": {"type": "NUMBER"},
                        "ui_element_id": {"type": "STRING"},
                        "text": {"type": "STRING"},
                        "normalized": {"type": "BOOLEAN"},
                        "color": {"type": "STRING"},
                        "font_size": {"type": "INTEGER"},
                        "font_family": {"type": "STRING"},
                        "align": {"type": "STRING"},
                        "baseline": {"type": "STRING"},
                        "max_width": {"type": "INTEGER"},
                        "panel_bg": {"type": "STRING"},
                        "panel_bg_secondary": {"type": "STRING"},
                        "accent_glow": {"type": "STRING"},
                        "panel_border": {"type": "STRING"},
                        "ttl_ms": {"type": "INTEGER"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "overlay_draw_pointer",
                "description": (
                    "Draw a pointer (dot/ring/line) with optional label to explain an on-screen target."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "x": {"type": "NUMBER"},
                        "y": {"type": "NUMBER"},
                        "ui_element_id": {"type": "STRING"},
                        "text": {"type": "STRING"},
                        "text_x": {"type": "NUMBER"},
                        "text_y": {"type": "NUMBER"},
                        "normalized": {"type": "BOOLEAN"},
                        "color": {"type": "STRING"},
                        "dot_color": {"type": "STRING"},
                        "radius": {"type": "NUMBER"},
                        "ring_radius": {"type": "NUMBER"},
                        "ring_width": {"type": "NUMBER"},
                        "line_width": {"type": "NUMBER"},
                        "font_size": {"type": "INTEGER"},
                        "font_family": {"type": "STRING"},
                        "text_max_width": {"type": "INTEGER"},
                        "ttl_ms": {"type": "INTEGER"},
                    },
                },
            },
            {
                "name": "overlay_clear",
                "description": "Clear all teaching/explanation annotations from the user's screen overlay.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "overlay_remove",
                "description": "Remove one annotation by id from the user's screen overlay.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                    },
                    "required": ["id"],
                },
            },
            {
                "name": "get_action_status",
                "description": "Get the current status of a brokered desktop action.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action_id": {"type": "STRING"},
                    },
                    "required": ["action_id"],
                },
            },
            {
                "name": "wait_for_action",
                "description": "Wait for a brokered desktop action to settle.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action_id": {"type": "STRING"},
                        "timeout_ms": {"type": "INTEGER"},
                    },
                    "required": ["action_id"],
                },
            },
        ]

    def get_declarations(self, *, read_only_only: bool = False) -> list[dict[str, Any]]:
        all_declarations = self.declarations
        if not read_only_only:
            return all_declarations
        return [
            item
            for item in all_declarations
            if str(item.get("name") or "") in self.READ_ONLY_TOOL_NAMES
        ]

    @staticmethod
    def _guidance_mode_rejection(tool_name: str) -> dict[str, Any]:
        clean_name = str(tool_name or "").strip() or "unknown_tool"
        return {
            "ok": False,
            "success": False,
            "error": "guidance_mode_read_only",
            "message": (
                f"Tool '{clean_name}' is disabled while Gemini Live is in guidance mode. "
                "Guide the user with text/voice instead of taking actions."
            ),
        }

    def execute(self, name: str, args: Optional[dict[str, Any]]) -> dict[str, Any]:
        tool_name = str(name or "").strip()
        payload = dict(args or {})

        if self._guidance_mode and tool_name in self.MUTATING_TOOL_NAMES:
            return self._guidance_mode_rejection(tool_name)

        if tool_name == "mouse_click":
            return self._queue_action(tool_name, payload, self._handle_mouse_click)
        if tool_name == "keyboard_type_text":
            return self._queue_action(tool_name, payload, self._handle_type_text)
        if tool_name == "keyboard_press_key":
            return self._queue_action(tool_name, payload, self._handle_press_key)
        if tool_name == "keyboard_key_combo":
            return self._queue_action(tool_name, payload, self._handle_key_combo)
        if tool_name == "app_open":
            return self._queue_action(tool_name, payload, self._handle_open_app)
        if tool_name == "workspace_switch":
            return self._queue_action(tool_name, payload, self._handle_workspace_switch)
        if tool_name == "ui_focus_window":
            return self._queue_action(tool_name, payload, self._handle_focus_window)
        if tool_name == "ui_get_snapshot":
            return self._handle_get_snapshot(payload)
        if tool_name == "ui_list_windows":
            return self._handle_list_windows(payload)
        if tool_name == "ui_read_text":
            return self._handle_read_text(payload)
        if tool_name == "capture_screen":
            return self._handle_capture_screen()
        if tool_name == "overlay_draw_box":
            return self._handle_overlay_draw_box(payload)
        if tool_name == "overlay_draw_text":
            return self._handle_overlay_draw_text(payload)
        if tool_name == "overlay_draw_pointer":
            return self._handle_overlay_draw_pointer(payload)
        if tool_name == "overlay_clear":
            return self._handle_overlay_clear()
        if tool_name == "overlay_remove":
            return self._handle_overlay_remove(payload)
        if tool_name == "get_action_status":
            return self.broker.get_action_status(str(payload.get("action_id") or ""))
        if tool_name == "wait_for_action":
            return self.broker.wait_for_action(
                str(payload.get("action_id") or ""),
                int(payload.get("timeout_ms") or 1000),
            )

        return {"ok": False, "error": "unknown_tool", "message": f"Unknown tool: {tool_name}"}

    def _queue_action(
        self,
        name: str,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any], Any], dict[str, Any]],
    ) -> dict[str, Any]:
        if self._guidance_mode and str(name or "").strip() in self.MUTATING_TOOL_NAMES:
            return self._guidance_mode_rejection(name)
        return self.broker.submit(
            name=name,
            args=args,
            handler=lambda *, cancel_event: handler(args, cancel_event),
        )

    @property
    def _desktop_manager(self):
        if self.agent.active_workspace == "agent":
            return self.agent.desktop_manager
        return None

    def _handle_mouse_click(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before click."}

        ui_element_id = str(args.get("ui_element_id") or "").strip()
        if ui_element_id:
            return self.agent.action_executor._execute_click({"ui_element_id": ui_element_id}, [])

        x = args.get("x")
        y = args.get("y")
        if x is None or y is None:
            return {"success": False, "message": "mouse_click requires x/y or ui_element_id", "error": "invalid_args"}

        button = str(args.get("button") or "left").strip().lower()
        clicks = max(1, int(args.get("clicks") or 1))
        if button not in {"left", "right", "middle"}:
            return {"success": False, "message": "button must be left, right, or middle", "error": "invalid_args"}

        dm = self._desktop_manager
        if dm is not None:
            if button != "left" or clicks != 1:
                return {
                    "success": False,
                    "message": "Agent workspace currently supports only a single left click.",
                    "error": "unsupported_agent_click",
                }
            clicked = mouse.click_at(int(x), int(y), desktop_manager=dm)
            time.sleep(Config.WAIT_AFTER_CLICK)
            return {
                "success": bool(clicked),
                "message": f"Clicked at ({int(x)}, {int(y)})" if clicked else "Failed to click",
                "payload": {"x": int(x), "y": int(y), "button": button, "clicks": clicks},
            }

        pyautogui.click(x=int(x), y=int(y), button=button, clicks=clicks, interval=0.07)
        time.sleep(Config.WAIT_AFTER_CLICK)
        return {
            "success": True,
            "message": f"Clicked at ({int(x)}, {int(y)})",
            "payload": {"x": int(x), "y": int(y), "button": button, "clicks": clicks},
        }

    def _handle_type_text(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before typing."}
        payload = {"text": str(args.get("text") or "")}
        ui_element_id = str(args.get("ui_element_id") or "").strip()
        if ui_element_id:
            payload["ui_element_id"] = ui_element_id
        return self.agent.action_executor._execute_type_text(payload)

    def _handle_press_key(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before key press."}
        payload = {"key": str(args.get("key") or "")}
        presses = max(1, int(args.get("presses") or 1))
        results = []
        for _ in range(presses):
            if cancel_event.is_set():
                return {"success": False, "cancelled": True, "message": "Action cancelled during key presses."}
            results.append(self.agent.action_executor._execute_press_key(payload))
        success = all(item.get("success") for item in results)
        return {
            "success": success,
            "message": f"Pressed key {payload['key']} x{presses}" if success else f"Failed to press key {payload['key']}",
            "payload": {"results": results},
        }

    def _handle_key_combo(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before key combo."}
        return self.agent.action_executor._execute_key_combo({"keys": list(args.get("keys") or [])})

    def _handle_open_app(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before app launch."}
        return self.agent.action_executor._execute_open_app({"app_name": args.get("app_name")})

    def _handle_workspace_switch(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before workspace switch."}
        return self.agent.action_executor._execute_switch_workspace({"workspace": args.get("workspace")})

    def _handle_focus_window(self, args: dict[str, Any], cancel_event) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"success": False, "cancelled": True, "message": "Action cancelled before focusing a window."}
        return self.agent.action_executor._execute_focus_window(args)

    def _handle_get_snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        goal_terms = [str(term).strip() for term in (args.get("goal_terms") or []) if str(term).strip()]
        snapshot = ui_automation.get_snapshot(
            self.agent.active_workspace,
            self._desktop_manager,
            max(Config.UIA_MAX_ELEMENTS * 3, 240),
            goal_terms or self.agent._goal_terms(),
        )
        summary = self._summarize_snapshot(snapshot)
        self.agent.current_blind_snapshot = snapshot
        self.last_snapshot_summary = summary
        return {"ok": True, "result": summary}

    def _handle_list_windows(self, args: dict[str, Any]) -> dict[str, Any]:
        result = ui_automation.list_windows(
            self.agent.active_workspace,
            self._desktop_manager,
            title_contains=str(args.get("title_contains") or ""),
            process_name=str(args.get("process_name") or ""),
            visible_only=bool(args.get("visible_only", False)),
            max_windows=int(args.get("max_windows") or Config.UIA_MAX_WINDOWS),
        )
        return {
            "ok": result.get("status") == "ok",
            "result": result,
            "message": f"Found {result.get('windows_count', 0)} window(s)." if result.get("status") == "ok" else "Window listing failed.",
        }

    def _handle_read_text(self, args: dict[str, Any]) -> dict[str, Any]:
        result = ui_automation.read_text(
            self.agent.active_workspace,
            self._desktop_manager,
            target=str(args.get("target") or "auto"),
            ui_element_id=str(args.get("ui_element_id") or "").strip() or None,
            max_chars=int(args.get("max_chars") or Config.UIA_TEXT_MAX_CHARS),
            use_ocr_fallback=bool(args.get("use_ocr_fallback", Config.UIA_TEXT_USE_OCR_FALLBACK_DEFAULT)),
            force_ocr=bool(args.get("force_ocr", False)),
            ocr_min_chars=int(args.get("ocr_min_chars") or Config.UIA_TEXT_OCR_MIN_CHARS),
            ocr_max_noise_ratio=float(
                args.get("ocr_max_noise_ratio") or Config.UIA_TEXT_OCR_MAX_NOISE_RATIO
            ),
        )
        return {
            "ok": result.get("status") == "ok",
            "result": result,
            "message": result.get("reason") or ("Read text." if result.get("status") == "ok" else "Text read failed."),
        }

    def _handle_capture_screen(self) -> dict[str, Any]:
        if self.broker.has_pending():
            return {
                "ok": False,
                "error": "action_in_progress",
                "message": "Cannot capture while another action is queued or running.",
                "active_action": self.broker.current_action_payload(),
            }

        elements, _ref_sheet = self.agent.capture_screen()
        screenshot_path = Config.SCREENSHOT_PATH if os.path.exists(Config.SCREENSHOT_PATH) else None
        debug_path = Config.DEBUG_PATH if os.path.exists(Config.DEBUG_PATH) else None
        ref_path = Config.REF_PATH if os.path.exists(Config.REF_PATH) else None

        summary = {
            "workspace": self.agent.active_workspace,
            "screenshot_path": screenshot_path,
            "debug_overlay_path": debug_path,
            "reference_sheet_path": ref_path,
            "elements_count": len(elements or []),
            "elements_preview": [
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "type": item.get("type"),
                    "x": item.get("x"),
                    "y": item.get("y"),
                }
                for item in (elements or [])[:20]
            ],
        }
        self.last_capture_summary = summary
        if screenshot_path and self.on_capture_ready:
            try:
                self.on_capture_ready(screenshot_path, summary)
            except Exception:
                logger.debug("Failed to send capture callback", exc_info=True)
        return {"ok": True, "result": summary, "message": "High-resolution capture completed."}

    @staticmethod
    def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
        try:
            return int(value)
        except Exception:
            return default

    def _rect_from_raw(self, rect: Any) -> Optional[dict[str, int]]:
        if not isinstance(rect, dict):
            return None
        left = self._to_int(rect.get("left"))
        top = self._to_int(rect.get("top"))
        right = self._to_int(rect.get("right"))
        bottom = self._to_int(rect.get("bottom"))
        if None in {left, top, right, bottom}:
            return None
        width = max(1, int(right) - int(left))
        height = max(1, int(bottom) - int(top))
        return {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
            "width": int(width),
            "height": int(height),
        }

    @staticmethod
    def _rect_area(rect: dict[str, int]) -> int:
        return max(1, int(rect.get("width", 1))) * max(1, int(rect.get("height", 1)))

    @staticmethod
    def _rect_contains(outer: dict[str, int], inner: dict[str, int], tolerance: int = 2) -> bool:
        return (
            int(inner["left"]) >= (int(outer["left"]) - tolerance)
            and int(inner["top"]) >= (int(outer["top"]) - tolerance)
            and int(inner["right"]) <= (int(outer["right"]) + tolerance)
            and int(inner["bottom"]) <= (int(outer["bottom"]) + tolerance)
        )

    @staticmethod
    def _normalize_label(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return " ".join(text.split())

    def _inset_rect(self, rect: dict[str, int], inset_px: Optional[int]) -> dict[str, int]:
        width = max(1, int(rect["width"]))
        height = max(1, int(rect["height"]))
        auto_inset = int(round(min(width, height) * 0.06))
        inset = max(0, int(inset_px if inset_px is not None else max(1, min(10, auto_inset))))
        max_inset = max(0, min((width - 6) // 2, (height - 6) // 2))
        inset = min(inset, max_inset)
        if inset <= 0:
            return dict(rect)

        left = int(rect["left"]) + inset
        top = int(rect["top"]) + inset
        right = int(rect["right"]) - inset
        bottom = int(rect["bottom"]) - inset
        width = max(1, right - left)
        height = max(1, bottom - top)
        return {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
            "width": int(width),
            "height": int(height),
        }

    def _refine_rect_from_elements(
        self,
        *,
        target_id: str,
        base_rect: dict[str, int],
        target_element: Optional[dict[str, Any]],
        elements: list[dict[str, Any]],
    ) -> dict[str, int]:
        if not elements:
            return base_rect

        base_area = float(self._rect_area(base_rect))
        if base_area <= 0.0:
            return base_rect

        target_name = self._normalize_label((target_element or {}).get("name"))
        target_type = str((target_element or {}).get("control_type") or "")

        precise_types = {
            "TextControl",
            "ButtonControl",
            "MenuItemControl",
            "TabItemControl",
            "EditControl",
            "HyperlinkControl",
            "ListItemControl",
            "TreeItemControl",
            "DataItemControl",
            "ImageControl",
            "CheckBoxControl",
            "RadioButtonControl",
        }
        broad_types = {"PaneControl", "GroupControl", "WindowControl", "DocumentControl", "CustomControl"}

        base_cx = (float(base_rect["left"]) + float(base_rect["right"])) * 0.5
        base_cy = (float(base_rect["top"]) + float(base_rect["bottom"])) * 0.5
        base_span = max(1.0, float(max(base_rect["width"], base_rect["height"])))

        ranked: list[tuple[float, int, dict[str, int]]] = []
        for element in elements:
            if str(element.get("ui_element_id") or "").strip() == target_id:
                continue
            candidate_rect = self._rect_from_raw(element.get("rect"))
            if candidate_rect is None:
                continue
            if not self._rect_contains(base_rect, candidate_rect, tolerance=3):
                continue

            candidate_area = self._rect_area(candidate_rect)
            ratio = candidate_area / max(1.0, base_area)
            if ratio >= 0.98:
                continue

            control_type = str(element.get("control_type") or "")
            candidate_name = self._normalize_label(element.get("name"))
            score = 0.0

            if control_type in precise_types:
                score += 2.0
            if control_type in broad_types:
                score -= 1.0
            if target_type in broad_types and control_type in precise_types:
                score += 1.2

            if target_name and candidate_name:
                if candidate_name == target_name:
                    score += 4.0
                elif target_name in candidate_name or candidate_name in target_name:
                    score += 2.3
            elif candidate_name:
                score += 0.4

            if 0.05 <= ratio <= 0.80:
                score += 1.0
            elif ratio < 0.02:
                score -= 0.3

            cand_cx = (float(candidate_rect["left"]) + float(candidate_rect["right"])) * 0.5
            cand_cy = (float(candidate_rect["top"]) + float(candidate_rect["bottom"])) * 0.5
            center_dist = abs(cand_cx - base_cx) + abs(cand_cy - base_cy)
            score += max(0.0, 1.0 - (center_dist / base_span))

            ranked.append((score, candidate_area, candidate_rect))

        if not ranked:
            return base_rect

        ranked.sort(key=lambda item: (-item[0], item[1]))
        best_score, _best_area, best_rect = ranked[0]
        if best_score < 2.0:
            return base_rect
        return best_rect

    @classmethod
    def _is_browser_snapshot(cls, snapshot: Optional[dict[str, Any]]) -> bool:
        if not isinstance(snapshot, dict):
            return False

        active_class = str(snapshot.get("active_window_class") or "").strip().lower()
        if any(hint in active_class for hint in cls.BROWSER_CLASS_HINTS):
            return True

        for window in (snapshot.get("windows") or []):
            process_name = str(window.get("process_name") or "").strip().lower()
            if process_name in cls.BROWSER_PROCESS_NAMES:
                return True
        return False

    @classmethod
    def _target_is_tiny_or_ambiguous(
        cls,
        rect: dict[str, int],
        target_element: Optional[dict[str, Any]],
    ) -> bool:
        width = max(1, int(rect.get("width", 1)))
        height = max(1, int(rect.get("height", 1)))
        area = width * height
        if width <= cls.SMALL_TARGET_SIDE_PX or height <= cls.SMALL_TARGET_SIDE_PX:
            return True
        if area <= cls.SMALL_TARGET_AREA_PX2:
            return True

        control_type = str((target_element or {}).get("control_type") or "").strip()
        name = str((target_element or {}).get("name") or "").strip()
        if control_type in {"PaneControl", "GroupControl", "CustomControl", "DocumentControl"}:
            return True
        if not name:
            return True
        return False

    @staticmethod
    def _hint_tokens(*values: Any) -> set[str]:
        tokens: set[str] = set()
        for value in values:
            for part in str(value or "").lower().replace("_", " ").split():
                clean = "".join(ch for ch in part if ch.isalnum())
                if len(clean) >= 3:
                    tokens.add(clean)
        return tokens

    @staticmethod
    def _rect_from_vision_element(element: dict[str, Any]) -> Optional[dict[str, int]]:
        try:
            cx = float(element.get("x"))
            cy = float(element.get("y"))
            w = max(2.0, float(element.get("w") or 0.0))
            h = max(2.0, float(element.get("h") or 0.0))
        except Exception:
            return None

        left = int(round(cx - (w / 2.0)))
        top = int(round(cy - (h / 2.0)))
        right = int(round(cx + (w / 2.0)))
        bottom = int(round(cy + (h / 2.0)))
        width = max(1, right - left)
        height = max(1, bottom - top)
        return {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
            "width": int(width),
            "height": int(height),
        }

    def _select_best_vision_element(
        self,
        *,
        elements: list[dict[str, Any]],
        anchor_rect: dict[str, int],
        target_tokens: set[str],
    ) -> Optional[dict[str, int]]:
        if not elements:
            return None

        anchor_cx = (float(anchor_rect["left"]) + float(anchor_rect["right"])) * 0.5
        anchor_cy = (float(anchor_rect["top"]) + float(anchor_rect["bottom"])) * 0.5
        anchor_span = max(1.0, float(max(anchor_rect["width"], anchor_rect["height"])))
        anchor_area = float(self._rect_area(anchor_rect))

        best_score = float("-inf")
        best_rect: Optional[dict[str, int]] = None
        for element in elements:
            candidate = self._rect_from_vision_element(element)
            if candidate is None:
                continue

            cand_cx = (float(candidate["left"]) + float(candidate["right"])) * 0.5
            cand_cy = (float(candidate["top"]) + float(candidate["bottom"])) * 0.5
            distance = abs(cand_cx - anchor_cx) + abs(cand_cy - anchor_cy)
            distance_score = max(0.0, 2.2 - (distance / anchor_span))

            overlap_score = 0.0
            if self._rect_contains(anchor_rect, candidate, tolerance=6):
                overlap_score += 2.0
            elif self._rect_contains(candidate, anchor_rect, tolerance=6):
                overlap_score += 0.8

            label = self._normalize_label(element.get("label"))
            type_name = self._normalize_label(element.get("type"))
            token_score = 0.0
            if target_tokens:
                bag = f"{label} {type_name}".strip()
                hits = sum(1 for token in target_tokens if token in bag)
                token_score += min(2.4, hits * 0.9)

            candidate_area = float(self._rect_area(candidate))
            ratio = candidate_area / max(1.0, anchor_area)
            size_score = 0.0
            if 0.08 <= ratio <= 2.5:
                size_score += 0.7
            elif ratio > 8.0:
                size_score -= 0.8

            score = overlap_score + distance_score + token_score + size_score
            if score > best_score:
                best_score = score
                best_rect = candidate

        return best_rect

    @staticmethod
    def _strip_browser_title_suffix(title: str) -> str:
        value = str(title or "").strip()
        if not value:
            return ""
        suffixes = (
            " - google chrome",
            " - chrome",
            " - microsoft edge",
            " - edge",
            " - mozilla firefox",
            " - firefox",
            " - brave",
            " - opera",
            " - vivaldi",
            " - arc",
        )
        lower = value.lower()
        for suffix in suffixes:
            if lower.endswith(suffix):
                return value[: -len(suffix)].strip(" -")
        return value

    def _task_tokens(self, text: str) -> set[str]:
        tokens = self._hint_tokens(text)
        return {token for token in tokens if token not in self.TOKEN_STOPWORDS}

    def _is_content_highlight_intent(self, text: str) -> bool:
        tokens = self._task_tokens(text)
        if not tokens:
            return False
        content_hits = len(tokens.intersection(self.CONTENT_INTENT_TERMS))
        control_hits = len(tokens.intersection(self.CONTROL_INTENT_TERMS))
        return content_hits > 0 and content_hits >= control_hits

    def _looks_like_browser_chrome_control(self, target_element: Optional[dict[str, Any]]) -> bool:
        if not isinstance(target_element, dict):
            return False
        control_type = str(target_element.get("control_type") or "").strip().lower()
        if control_type in {"editcontrol", "menuitemcontrol", "tabitemcontrol", "buttoncontrol", "titlebarcontrol"}:
            return True

        bag = " ".join(
            str(target_element.get(key) or "").lower().replace(" ", "")
            for key in ("name", "automation_id", "class_name")
        )
        return any(term in bag for term in self.BROWSER_CHROME_TERMS)

    def _dom_target_tokens(
        self,
        target_element: Optional[dict[str, Any]],
        active_title: str,
        current_task: str,
    ) -> list[str]:
        generic = {
            "tab",
            "window",
            "page",
            "document",
            "group",
            "pane",
            "custom",
            "chrome",
            "edge",
            "firefox",
            "brave",
            "browser",
        }
        target_tokens = self._hint_tokens(
            (target_element or {}).get("name"),
            (target_element or {}).get("automation_id"),
            (target_element or {}).get("class_name"),
        )
        task_tokens = self._task_tokens(current_task)

        tokens = set(target_tokens)
        content_intent = self._is_content_highlight_intent(current_task)
        chrome_control = self._looks_like_browser_chrome_control(target_element)
        if content_intent and chrome_control:
            tokens = set(task_tokens)
        else:
            tokens.update(task_tokens)
            tokens.update(self._task_tokens(self._strip_browser_title_suffix(active_title)))

        filtered = [token for token in tokens if token not in generic and len(token) >= 3]
        filtered.sort(key=len, reverse=True)
        return filtered[:8]

    def _http_json(self, url: str) -> Optional[Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.CDP_HTTP_TIMEOUT_S) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            return None

    def _list_cdp_targets(self) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for port in self.CDP_PORT_CANDIDATES:
            payload = self._http_json(f"http://127.0.0.1:{port}/json/list")
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ws_url = str(item.get("webSocketDebuggerUrl") or "").strip()
                if not ws_url:
                    continue
                if str(item.get("type") or "").strip().lower() != "page":
                    continue
                cloned = dict(item)
                cloned["_port"] = port
                targets.append(cloned)
        return targets

    def _select_cdp_target(self, targets: list[dict[str, Any]], active_title: str) -> Optional[dict[str, Any]]:
        if not targets:
            return None
        clean_active = self._strip_browser_title_suffix(active_title).lower()
        active_tokens = self._hint_tokens(clean_active)

        best_target = None
        best_score = float("-inf")
        for target in targets:
            title = str(target.get("title") or "").strip()
            url = str(target.get("url") or "").strip().lower()
            title_tokens = self._hint_tokens(title)
            score = 0.0
            if clean_active:
                normalized_title = title.lower()
                if clean_active == normalized_title:
                    score += 8.0
                elif clean_active and clean_active in normalized_title:
                    score += 4.5
                overlap = len(active_tokens.intersection(title_tokens))
                score += overlap * 1.6
            if target.get("attached"):
                score += 0.8
            if url.startswith("http://") or url.startswith("https://"):
                score += 0.5
            if "new tab" in title.lower():
                score -= 1.0
            if score > best_score:
                best_score = score
                best_target = target

        return best_target

    def _run_cdp_eval_async(self, ws_url: str, expression: str) -> Optional[Any]:
        try:
            import websockets
        except Exception:
            return None

        async def _runner() -> Optional[Any]:
            payload = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            }
            async with websockets.connect(
                ws_url,
                open_timeout=self.CDP_WS_TIMEOUT_S,
                close_timeout=self.CDP_WS_TIMEOUT_S,
                ping_interval=None,
                max_size=1024 * 1024,
            ) as websocket:
                await websocket.send(json.dumps(payload))
                while True:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=self.CDP_WS_TIMEOUT_S)
                    message = json.loads(raw)
                    if int(message.get("id", -1)) != 1:
                        continue
                    value = ((message.get("result") or {}).get("result") or {}).get("value")
                    if isinstance(value, dict):
                        return value
                    return None

        try:
            return asyncio.run(_runner())
        except Exception:
            return None

    def _run_cdp_eval_sync(self, ws_url: str, expression: str) -> Optional[Any]:
        try:
            from websockets.sync.client import connect as ws_connect
        except Exception:
            return None

        payload = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
        }
        try:
            with ws_connect(
                ws_url,
                open_timeout=self.CDP_WS_TIMEOUT_S,
                close_timeout=self.CDP_WS_TIMEOUT_S,
                max_size=1024 * 1024,
            ) as websocket:
                websocket.send(json.dumps(payload))
                deadline = time.monotonic() + self.CDP_WS_TIMEOUT_S
                while time.monotonic() < deadline:
                    timeout = max(0.05, deadline - time.monotonic())
                    raw = websocket.recv(timeout=timeout)
                    message = json.loads(raw)
                    if int(message.get("id", -1)) != 1:
                        continue
                    value = ((message.get("result") or {}).get("result") or {}).get("value")
                    if isinstance(value, dict):
                        return value
                    return None
        except Exception:
            return None
        return None

    def _cdp_eval(self, ws_url: str, expression: str) -> Optional[Any]:
        value = self._run_cdp_eval_sync(ws_url, expression)
        if value is not None:
            return value
        return self._run_cdp_eval_async(ws_url, expression)

    @staticmethod
    def _dom_candidate_js_expression(tokens: list[str]) -> str:
        token_json = json.dumps(tokens)
        return f"""
(() => {{
  const tokens = {token_json};
  const normalize = (v) => (v || '').toString().toLowerCase().replace(/\\s+/g, ' ').trim();
  const isVisible = (el, rect) => {{
    if (!rect || rect.width < 2 || rect.height < 2) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const opacity = parseFloat(style.opacity || '1');
    if (Number.isFinite(opacity) && opacity < 0.1) return false;
    const inViewport = rect.bottom >= 0 && rect.right >= 0 && rect.left <= window.innerWidth && rect.top <= window.innerHeight;
    return !!inViewport;
  }};

  const selectors = [
    'a','button','input','textarea','select','option','label',
    'h1','h2','h3','h4','h5','h6','p','span','div',
    '[role]','[aria-label]','[title]','[name]'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(selectors)).slice(0, 2200);
  const results = [];

  for (const el of nodes) {{
    const rect = el.getBoundingClientRect();
    if (!isVisible(el, rect)) continue;
    const attrs = [
      el.innerText,
      el.textContent,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('name'),
      el.getAttribute('placeholder'),
      el.getAttribute('id'),
      el.className,
      el.getAttribute('role'),
      el.tagName
    ];
    const bag = normalize(attrs.join(' ')).slice(0, 420);
    let tokenHits = 0;
    for (const token of tokens) {{
      if (token && bag.includes(token)) tokenHits += 1;
    }}
    if (tokens.length > 0 && tokenHits === 0) continue;

    const tag = normalize(el.tagName);
    const role = normalize(el.getAttribute('role'));
    let score = tokenHits * 3.0;
    if (tag === 'button' || role === 'button') score += 1.2;
    if (tag === 'a' || role === 'link') score += 0.9;
    if (tag === 'input' || tag === 'textarea' || role === 'textbox') score += 1.0;

    results.push({{
      score,
      tokenHits,
      bag,
      tag,
      role,
      rect: {{
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height
      }}
    }});
  }}

  results.sort((a, b) => (b.score - a.score) || (a.rect.width * a.rect.height - b.rect.width * b.rect.height));
  const vv = window.visualViewport;
  return {{
    metrics: {{
      screenX: window.screenX ?? window.screenLeft ?? 0,
      screenY: window.screenY ?? window.screenTop ?? 0,
      outerWidth: window.outerWidth ?? 0,
      outerHeight: window.outerHeight ?? 0,
      innerWidth: window.innerWidth ?? 0,
      innerHeight: window.innerHeight ?? 0,
      dpr: window.devicePixelRatio ?? 1,
      viewportOffsetLeft: vv ? (vv.offsetLeft || 0) : 0,
      viewportOffsetTop: vv ? (vv.offsetTop || 0) : 0
    }},
    candidates: results.slice(0, 36)
  }};
}})();
""".strip()

    @staticmethod
    def _dom_rect_to_abs(
        rect: dict[str, Any],
        metrics: dict[str, Any],
    ) -> Optional[dict[str, int]]:
        try:
            left = float(rect.get("left"))
            top = float(rect.get("top"))
            right = float(rect.get("right"))
            bottom = float(rect.get("bottom"))
            screen_x = float(metrics.get("screenX", 0.0))
            screen_y = float(metrics.get("screenY", 0.0))
            outer_w = float(metrics.get("outerWidth", 0.0))
            outer_h = float(metrics.get("outerHeight", 0.0))
            inner_w = float(metrics.get("innerWidth", 0.0))
            inner_h = float(metrics.get("innerHeight", 0.0))
            dpr = max(1.0, float(metrics.get("dpr", 1.0) or 1.0))
            vv_left = float(metrics.get("viewportOffsetLeft", 0.0))
            vv_top = float(metrics.get("viewportOffsetTop", 0.0))
        except Exception:
            return None

        chrome_x = max(0.0, (outer_w - inner_w) * 0.5)
        chrome_y = max(0.0, outer_h - inner_h)
        abs_left = (screen_x + chrome_x + vv_left + left) * dpr
        abs_top = (screen_y + chrome_y + vv_top + top) * dpr
        abs_right = (screen_x + chrome_x + vv_left + right) * dpr
        abs_bottom = (screen_y + chrome_y + vv_top + bottom) * dpr
        width = max(1, int(round(abs_right - abs_left)))
        height = max(1, int(round(abs_bottom - abs_top)))
        return {
            "left": int(round(abs_left)),
            "top": int(round(abs_top)),
            "right": int(round(abs_left)) + width,
            "bottom": int(round(abs_top)) + height,
            "width": int(width),
            "height": int(height),
        }

    def _resolve_browser_dom_rect(
        self,
        *,
        snapshot: Optional[dict[str, Any]],
        target_element: Optional[dict[str, Any]],
        anchor_rect: dict[str, int],
    ) -> Optional[dict[str, int]]:
        if not self._is_browser_snapshot(snapshot):
            return None

        active_title = str((snapshot or {}).get("active_window_title") or "")
        current_task = str(getattr(self.agent, "current_task", "") or "")
        content_intent = self._is_content_highlight_intent(current_task)
        chrome_control = self._looks_like_browser_chrome_control(target_element)
        tokens = self._dom_target_tokens(target_element, active_title, current_task)
        if not tokens:
            return None

        targets = self._list_cdp_targets()
        chosen = self._select_cdp_target(targets, active_title)
        if not chosen:
            return None

        ws_url = str(chosen.get("webSocketDebuggerUrl") or "").strip()
        if not ws_url:
            return None

        expression = self._dom_candidate_js_expression(tokens)
        result = self._cdp_eval(ws_url, expression)
        if not isinstance(result, dict):
            return None

        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        if not candidates:
            return None

        use_content_anchor = content_intent and chrome_control
        if use_content_anchor:
            inner_w = max(1.0, float(metrics.get("innerWidth", 1280.0) or 1280.0))
            inner_h = max(1.0, float(metrics.get("innerHeight", 800.0) or 800.0))
            dpr = max(1.0, float(metrics.get("dpr", 1.0) or 1.0))
            screen_x = float(metrics.get("screenX", 0.0))
            screen_y = float(metrics.get("screenY", 0.0))
            outer_w = float(metrics.get("outerWidth", inner_w))
            outer_h = float(metrics.get("outerHeight", inner_h))
            chrome_x = max(0.0, (outer_w - inner_w) * 0.5)
            chrome_y = max(0.0, outer_h - inner_h)
            content_cx = (screen_x + chrome_x + (inner_w * 0.52)) * dpr
            content_cy = (screen_y + chrome_y + (inner_h * 0.48)) * dpr
            anchor_cx = float(content_cx)
            anchor_cy = float(content_cy)
            anchor_span = max(320.0, inner_w * dpr * 0.45)
        else:
            anchor_cx = (float(anchor_rect["left"]) + float(anchor_rect["right"])) * 0.5
            anchor_cy = (float(anchor_rect["top"]) + float(anchor_rect["bottom"])) * 0.5
            anchor_span = max(1.0, float(max(anchor_rect["width"], anchor_rect["height"])))

        best_score = float("-inf")
        best_rect = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            rect = self._dom_rect_to_abs(candidate.get("rect") or {}, metrics)
            if rect is None:
                continue
            hits = int(candidate.get("tokenHits") or 0)
            base_score = float(candidate.get("score") or 0.0)
            cand_cx = (float(rect["left"]) + float(rect["right"])) * 0.5
            cand_cy = (float(rect["top"]) + float(rect["bottom"])) * 0.5
            distance = abs(cand_cx - anchor_cx) + abs(cand_cy - anchor_cy)
            distance_weight = 1.2 if use_content_anchor else 2.0
            distance_score = max(0.0, distance_weight - (distance / anchor_span))
            overlap_score = 0.0
            if not use_content_anchor and self._rect_contains(anchor_rect, rect, tolerance=24):
                overlap_score += 1.4
            elif not use_content_anchor and self._rect_contains(rect, anchor_rect, tolerance=24):
                overlap_score += 0.7

            score = base_score + (hits * 0.8) + distance_score + overlap_score
            if use_content_anchor:
                top_bias_limit = (anchor_cy - (anchor_span * 0.25))
                if float(rect["top"]) < top_bias_limit:
                    score -= 0.9
            if score > best_score:
                best_score = score
                best_rect = rect

        if best_rect is None:
            return None
        return best_rect

    def _browser_two_pass_vision_refine(
        self,
        *,
        base_rect: dict[str, int],
        target_element: Optional[dict[str, Any]],
        force_content_scan: bool = False,
    ) -> Optional[dict[str, int]]:
        capture = getattr(self.agent, "capture_screen", None)
        if not callable(capture):
            return None

        eye = getattr(self.agent, "robotics_eye", None)
        current_task = str(getattr(self.agent, "current_task", "") or "")
        if force_content_scan:
            target_tokens = self._task_tokens(current_task)
        else:
            target_tokens = self._hint_tokens(
                (target_element or {}).get("name"),
                (target_element or {}).get("automation_id"),
                (target_element or {}).get("class_name"),
                current_task,
            )

        try:
            elements, _ref_sheet = capture(force_robotics=True)
        except Exception:
            logger.debug("Browser vision pass-1 capture failed", exc_info=True)
            return None

        anchor_rect = dict(base_rect)
        if force_content_scan and os.path.exists(Config.SCREENSHOT_PATH):
            try:
                with Image.open(Config.SCREENSHOT_PATH) as image:
                    w, h = image.size
                cx = w * 0.52
                cy = h * 0.48
                anchor_rect = {
                    "left": int(cx - (w * 0.22)),
                    "top": int(cy - (h * 0.20)),
                    "right": int(cx + (w * 0.22)),
                    "bottom": int(cy + (h * 0.20)),
                    "width": int(max(1, w * 0.44)),
                    "height": int(max(1, h * 0.40)),
                }
            except Exception:
                anchor_rect = dict(base_rect)

        pass1_rect = self._select_best_vision_element(
            elements=list(elements or []),
            anchor_rect=anchor_rect,
            target_tokens=target_tokens,
        )
        if pass1_rect is None:
            return None

        screenshot_path = Config.SCREENSHOT_PATH if os.path.exists(Config.SCREENSHOT_PATH) else None
        if not screenshot_path or eye is None or not hasattr(eye, "get_screen_elements_with_boxes"):
            return pass1_rect

        pad_x = max(24, int(pass1_rect["width"] * 0.8))
        pad_y = max(24, int(pass1_rect["height"] * 0.8))
        try:
            with Image.open(screenshot_path) as image:
                image_width, image_height = image.size
                crop_left = max(0, int(pass1_rect["left"] - pad_x))
                crop_top = max(0, int(pass1_rect["top"] - pad_y))
                crop_right = min(image_width, int(pass1_rect["right"] + pad_x))
                crop_bottom = min(image_height, int(pass1_rect["bottom"] + pad_y))
                if crop_right - crop_left < 8 or crop_bottom - crop_top < 8:
                    return pass1_rect

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                image.crop((crop_left, crop_top, crop_right, crop_bottom)).save(tmp_path)
        except Exception:
            logger.debug("Browser vision pass-2 crop creation failed", exc_info=True)
            return pass1_rect

        try:
            second_elements = eye.get_screen_elements_with_boxes(tmp_path, max_elements=20) or []
        except Exception:
            logger.debug("Browser vision pass-2 detection failed", exc_info=True)
            return pass1_rect
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        if not second_elements:
            return pass1_rect

        crop_anchor = {
            "left": int(pass1_rect["left"] - crop_left),
            "top": int(pass1_rect["top"] - crop_top),
            "right": int(pass1_rect["right"] - crop_left),
            "bottom": int(pass1_rect["bottom"] - crop_top),
            "width": int(pass1_rect["width"]),
            "height": int(pass1_rect["height"]),
        }
        pass2_local = self._select_best_vision_element(
            elements=list(second_elements),
            anchor_rect=crop_anchor,
            target_tokens=target_tokens,
        )
        if pass2_local is None:
            return pass1_rect

        refined = {
            "left": int(crop_left + pass2_local["left"]),
            "top": int(crop_top + pass2_local["top"]),
            "right": int(crop_left + pass2_local["right"]),
            "bottom": int(crop_top + pass2_local["bottom"]),
            "width": int(pass2_local["width"]),
            "height": int(pass2_local["height"]),
        }
        return refined

    def _overlay_sender(self) -> Optional[Callable[[dict[str, Any]], None]]:
        chat_window = getattr(self.agent, "chat_window", None)
        sender = getattr(chat_window, "send_overlay_command", None)
        if callable(sender):
            return sender
        return None

    def _send_overlay_command(self, command: dict[str, Any], *, message: str) -> dict[str, Any]:
        sender = self._overlay_sender()
        if sender is None:
            return {
                "ok": False,
                "success": False,
                "error": "overlay_unavailable",
                "message": "Live annotation overlay is unavailable.",
            }
        try:
            sender(command)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to dispatch overlay command", exc_info=True)
            return {
                "ok": False,
                "success": False,
                "error": "overlay_dispatch_failed",
                "message": str(exc),
            }
        return {
            "ok": True,
            "success": True,
            "message": message,
            "result": {"annotation_id": command.get("id"), "command": command},
        }

    def _require_user_workspace_for_overlay(self) -> Optional[dict[str, Any]]:
        workspace = str(getattr(self.agent, "active_workspace", "user") or "user").strip().lower()
        if workspace == "user":
            return None
        return {
            "ok": False,
            "success": False,
            "error": "overlay_workspace_mismatch",
            "message": "Overlay annotations are supported only on the user workspace.",
            "workspace": workspace,
        }

    def _resolve_ui_element_rect(
        self,
        ui_element_id: str,
        *,
        tight: bool = True,
        inset_px: Optional[int] = None,
    ) -> Optional[dict[str, int]]:
        target_id = str(ui_element_id or "").strip()
        if not target_id:
            return None

        snapshots: list[dict[str, Any]] = []

        try:
            latest = ui_automation.get_snapshot(
                self.agent.active_workspace,
                self._desktop_manager,
                max(Config.UIA_MAX_ELEMENTS * 3, 240),
                self.agent._goal_terms(),
            )
            if isinstance(latest, dict):
                snapshots.append(latest)
                self.agent.current_blind_snapshot = latest
                self.last_snapshot_summary = self._summarize_snapshot(latest)
        except Exception:
            logger.debug("Unable to refresh UIA snapshot for overlay targeting", exc_info=True)

        current = getattr(self.agent, "current_blind_snapshot", None)
        if isinstance(current, dict) and (not snapshots or current is not snapshots[0]):
            snapshots.append(current)

        direct_rect = None
        try:
            direct_rect = ui_automation.get_element_rect(
                self.agent.active_workspace,
                self._desktop_manager,
                target_id,
            )
        except Exception:
            logger.debug("Unable to fetch direct UIA element rect for overlay targeting", exc_info=True)

        resolved_rect = self._rect_from_raw(direct_rect) if isinstance(direct_rect, dict) else None
        target_element: Optional[dict[str, Any]] = None
        target_pool: list[dict[str, Any]] = []
        matched_snapshot: Optional[dict[str, Any]] = None

        for snapshot in snapshots:
            elements = list(snapshot.get("elements") or [])
            for element in elements:
                if str(element.get("ui_element_id") or "").strip() != target_id:
                    continue
                candidate_rect = self._rect_from_raw(element.get("rect"))
                if target_element is None:
                    target_element = element
                    target_pool = elements
                    matched_snapshot = snapshot
                if resolved_rect is None and candidate_rect is not None:
                    resolved_rect = candidate_rect
                break
            if target_element is not None and resolved_rect is not None:
                break

        if resolved_rect is None:
            return None

        if target_pool:
            resolved_rect = self._refine_rect_from_elements(
                target_id=target_id,
                base_rect=resolved_rect,
                target_element=target_element,
                elements=target_pool,
            )

        if self._is_browser_snapshot(matched_snapshot):
            dom_rect = self._resolve_browser_dom_rect(
                snapshot=matched_snapshot,
                target_element=target_element,
                anchor_rect=resolved_rect,
            )
            if dom_rect is not None:
                resolved_rect = dom_rect

        content_intent = self._is_content_highlight_intent(str(getattr(self.agent, "current_task", "") or ""))
        chrome_control = self._looks_like_browser_chrome_control(target_element)
        needs_vision_refine = self._target_is_tiny_or_ambiguous(resolved_rect, target_element) or (
            content_intent and chrome_control
        )

        if self._is_browser_snapshot(matched_snapshot) and needs_vision_refine:
            refined = self._browser_two_pass_vision_refine(
                base_rect=resolved_rect,
                target_element=target_element,
                force_content_scan=bool(content_intent and chrome_control),
            )
            if refined is not None:
                resolved_rect = refined

        if tight:
            resolved_rect = self._inset_rect(resolved_rect, inset_px)
        return resolved_rect

    @staticmethod
    def _annotation_id(prefix: str, requested: Any) -> str:
        candidate = str(requested or "").strip()
        if candidate:
            return candidate
        return f"{prefix}_{time.monotonic_ns()}"

    def _handle_overlay_clear(self) -> dict[str, Any]:
        command = {"action": "overlay_clear"}
        return self._send_overlay_command(command, message="Overlay annotations cleared.")

    def _handle_overlay_remove(self, args: dict[str, Any]) -> dict[str, Any]:
        annotation_id = str(args.get("id") or "").strip()
        if not annotation_id:
            return {
                "ok": False,
                "success": False,
                "error": "invalid_args",
                "message": "overlay_remove requires id.",
            }
        command = {"action": "overlay_remove", "id": annotation_id}
        return self._send_overlay_command(command, message=f"Overlay annotation removed: {annotation_id}")

    def _handle_overlay_draw_box(self, args: dict[str, Any]) -> dict[str, Any]:
        guard = self._require_user_workspace_for_overlay()
        if guard:
            return guard

        annotation_id = self._annotation_id("box", args.get("id"))
        command: dict[str, Any] = {"action": "overlay_draw_box", "id": annotation_id}

        ui_element_id = str(args.get("ui_element_id") or "").strip()
        if ui_element_id:
            tight = bool(args.get("tight", True))
            inset_px = self._to_int(args.get("inset_px"), None)
            rect = self._resolve_ui_element_rect(ui_element_id, tight=tight, inset_px=inset_px)
            if not rect:
                return {
                    "ok": False,
                    "success": False,
                    "error": "uia_target_not_found",
                    "message": f"Could not resolve ui_element_id '{ui_element_id}' for overlay_draw_box.",
                }
            command.update(
                {
                    "x": rect["left"],
                    "y": rect["top"],
                    "width": rect["width"],
                    "height": rect["height"],
                    "normalized": False,
                }
            )
        else:
            x = args.get("x")
            y = args.get("y")
            width = args.get("width")
            height = args.get("height")
            if None not in {x, y, width, height}:
                command.update({"x": x, "y": y, "width": width, "height": height})
                command["normalized"] = bool(args.get("normalized", False))
            else:
                x_min = args.get("x_min")
                y_min = args.get("y_min")
                x_max = args.get("x_max")
                y_max = args.get("y_max")
                if None in {x_min, y_min, x_max, y_max}:
                    return {
                        "ok": False,
                        "success": False,
                        "error": "invalid_args",
                        "message": (
                            "overlay_draw_box requires either x/y/width/height, "
                            "x_min/y_min/x_max/y_max, or ui_element_id."
                        ),
                    }
                command.update({"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max})
                command["normalized"] = bool(args.get("normalized", False))

        for key in ("color", "stroke_width", "opacity", "corner_radius", "ttl_ms"):
            if args.get(key) is not None:
                command[key] = args.get(key)
        if "stroke_width" not in command:
            command["stroke_width"] = 2.0
        if "corner_radius" not in command:
            command["corner_radius"] = 8.0
        if "ttl_ms" not in command:
            command["ttl_ms"] = self.DEFAULT_OVERLAY_BOX_TTL_MS

        return self._send_overlay_command(command, message=f"Overlay box drawn ({annotation_id}).")

    def _handle_overlay_draw_text(self, args: dict[str, Any]) -> dict[str, Any]:
        guard = self._require_user_workspace_for_overlay()
        if guard:
            return guard

        text = str(args.get("text") or "").strip()
        if not text:
            return {
                "ok": False,
                "success": False,
                "error": "invalid_args",
                "message": "overlay_draw_text requires text.",
            }

        annotation_id = self._annotation_id("text", args.get("id"))
        command: dict[str, Any] = {
            "action": "overlay_draw_text",
            "id": annotation_id,
            "text": text,
        }

        x = args.get("x")
        y = args.get("y")
        ui_element_id = str(args.get("ui_element_id") or "").strip()
        if x is None or y is None:
            if not ui_element_id:
                return {
                    "ok": False,
                    "success": False,
                    "error": "invalid_args",
                    "message": "overlay_draw_text requires x/y or ui_element_id.",
                }
            rect = self._resolve_ui_element_rect(ui_element_id)
            if not rect:
                return {
                    "ok": False,
                    "success": False,
                    "error": "uia_target_not_found",
                    "message": f"Could not resolve ui_element_id '{ui_element_id}' for overlay_draw_text.",
                }
            command["x"] = rect["left"]
            command["y"] = max(4, rect["top"] - 12)
            command["normalized"] = False
            command.setdefault("baseline", "bottom")
        else:
            command["x"] = x
            command["y"] = y
            command["normalized"] = bool(args.get("normalized", False))

        for key in (
            "color",
            "font_size",
            "font_family",
            "align",
            "baseline",
            "max_width",
            "panel_bg",
            "panel_bg_secondary",
            "accent_glow",
            "panel_border",
            "ttl_ms",
        ):
            if args.get(key) is not None:
                command[key] = args.get(key)
        if "ttl_ms" not in command:
            command["ttl_ms"] = self.DEFAULT_OVERLAY_TEXT_TTL_MS

        return self._send_overlay_command(command, message=f"Overlay text drawn ({annotation_id}).")

    def _handle_overlay_draw_pointer(self, args: dict[str, Any]) -> dict[str, Any]:
        guard = self._require_user_workspace_for_overlay()
        if guard:
            return guard

        annotation_id = self._annotation_id("ptr", args.get("id"))
        command: dict[str, Any] = {"action": "overlay_draw_pointer", "id": annotation_id}

        ui_element_id = str(args.get("ui_element_id") or "").strip()
        x = args.get("x")
        y = args.get("y")
        resolved_rect = None
        if x is None or y is None:
            if not ui_element_id:
                return {
                    "ok": False,
                    "success": False,
                    "error": "invalid_args",
                    "message": "overlay_draw_pointer requires x/y or ui_element_id.",
                }
            resolved_rect = self._resolve_ui_element_rect(ui_element_id)
            if not resolved_rect:
                return {
                    "ok": False,
                    "success": False,
                    "error": "uia_target_not_found",
                    "message": f"Could not resolve ui_element_id '{ui_element_id}' for overlay_draw_pointer.",
                }
            command["x"] = int(resolved_rect["left"] + (resolved_rect["width"] / 2))
            command["y"] = int(resolved_rect["top"] + (resolved_rect["height"] / 2))
            command["normalized"] = False
        else:
            command["x"] = x
            command["y"] = y
            command["normalized"] = bool(args.get("normalized", False))

        text = str(args.get("text") or "").strip()
        if text:
            command["text"] = text
            text_x = args.get("text_x")
            text_y = args.get("text_y")
            if text_x is None and resolved_rect is not None:
                text_x = resolved_rect["right"] + 16
            if text_y is None and resolved_rect is not None:
                text_y = max(6, resolved_rect["top"] - 8)
            if text_x is not None:
                command["text_x"] = text_x
            if text_y is not None:
                command["text_y"] = text_y

        for key in (
            "color",
            "dot_color",
            "radius",
            "ring_radius",
            "ring_width",
            "line_width",
            "font_size",
            "font_family",
            "text_max_width",
            "ttl_ms",
        ):
            if args.get(key) is not None:
                command[key] = args.get(key)
        if "ttl_ms" not in command:
            command["ttl_ms"] = self.DEFAULT_OVERLAY_POINTER_TTL_MS

        return self._send_overlay_command(command, message=f"Overlay pointer drawn ({annotation_id}).")

    @staticmethod
    def _summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except Exception:
                return int(default)

        def _area(item: dict[str, Any]) -> int:
            rect = item.get("rect") or {}
            left = _safe_int(rect.get("left"))
            top = _safe_int(rect.get("top"))
            right = _safe_int(rect.get("right"))
            bottom = _safe_int(rect.get("bottom"))
            return max(1, right - left) * max(1, bottom - top)

        source_elements = list(snapshot.get("elements") or [])
        precise_types = {
            "TextControl",
            "ButtonControl",
            "MenuItemControl",
            "TabItemControl",
            "EditControl",
            "HyperlinkControl",
            "ListItemControl",
            "TreeItemControl",
            "DataItemControl",
            "CheckBoxControl",
            "RadioButtonControl",
        }

        prioritized_precise = [
            item
            for item in source_elements
            if str(item.get("control_type") or "") in precise_types and str(item.get("name") or "").strip()
        ]
        prioritized_precise.sort(
            key=lambda item: (
                _area(item),
                -float(item.get("rank_score", 0.0) or 0.0),
            )
        )

        summary_limit = 80
        precise_limit = 50
        selected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in prioritized_precise[:precise_limit]:
            element_id = str(item.get("ui_element_id") or "").strip()
            if not element_id or element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            selected.append(item)

        for item in source_elements:
            if len(selected) >= summary_limit:
                break
            element_id = str(item.get("ui_element_id") or "").strip()
            if not element_id or element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            selected.append(item)

        elements = []
        for item in selected:
            rect = item.get("rect") or {}
            left = _safe_int(rect.get("left"))
            top = _safe_int(rect.get("top"))
            right = _safe_int(rect.get("right"))
            bottom = _safe_int(rect.get("bottom"))
            elements.append(
                {
                    "ui_element_id": item.get("ui_element_id"),
                    "name": item.get("name"),
                    "control_type": item.get("control_type"),
                    "automation_id": item.get("automation_id"),
                    "class_name": item.get("class_name"),
                    "rank_score": item.get("rank_score"),
                    "rect": {
                        "left": rect.get("left"),
                        "top": rect.get("top"),
                        "right": rect.get("right"),
                        "bottom": rect.get("bottom"),
                        "width": max(1, right - left),
                        "height": max(1, bottom - top),
                    },
                }
            )

        windows = []
        for item in (snapshot.get("windows") or [])[:20]:
            windows.append(
                {
                    "window_id": item.get("window_id"),
                    "title": item.get("title"),
                    "class_name": item.get("class_name"),
                    "process_name": item.get("process_name"),
                    "is_visible": item.get("is_visible"),
                    "is_minimized": item.get("is_minimized"),
                }
            )

        return {
            "workspace": snapshot.get("workspace"),
            "available": bool(snapshot.get("available", False)),
            "error": snapshot.get("error"),
            "active_window_title": snapshot.get("active_window_title"),
            "active_window_class": snapshot.get("active_window_class"),
            "elements_count": snapshot.get("elements_count", len(elements)),
            "windows_count": snapshot.get("windows_count", len(windows)),
            "elements": elements,
            "windows": windows,
        }
