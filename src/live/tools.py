from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any, Optional

import pyautogui

from config import Config
from live.broker import LiveActionBroker
import tools.mouse as mouse
from tools import ui_automation

logger = logging.getLogger("pixelpilot.live.tools")


class LiveToolRegistry:
    READ_ONLY_TOOL_NAMES = {
        "ui_get_snapshot",
        "ui_list_windows",
        "ui_read_text",
        "capture_screen",
        "get_action_status",
        "wait_for_action",
    }
    MUTATING_TOOL_NAMES = {
        "mouse_click",
        "keyboard_type_text",
        "keyboard_press_key",
        "keyboard_key_combo",
        "app_open",
        "workspace_switch",
        "ui_focus_window",
    }

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

