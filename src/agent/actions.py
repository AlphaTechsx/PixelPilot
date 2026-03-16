import time
import logging
import sys
import pyautogui
import tools.mouse as mouse
import tools.ui_automation as ui_automation
from typing import Any, Dict, List, Optional
from config import Config, OperationMode

logger = logging.getLogger("pixelpilot.actions")

class ActionExecutor:
    """
    Handles execution of individual agent actions.
    """
    def __init__(self, agent_orchestrator):
        """
        Args:
            agent_orchestrator: Reference to the parent AgentOrchestrator for access to state/skills.
        """
        self.agent = agent_orchestrator
    
    def log(self, message: str):
        self.agent.log(message)

    @staticmethod
    def _result(success: bool, message: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "success": bool(success),
            "message": str(message or ""),
            "payload": payload,
        }

    @property
    def desktop_manager(self):
        if self.agent.active_workspace == "agent":
            return self.agent.desktop_manager
        return None

    def execute(self, action: Dict[str, Any], elements: List[Dict]) -> Dict[str, Any]:
        """
        Dispatch method for executing actions.
        """
        if not isinstance(action, dict):
            logger.error(f"Invalid action payload type: {type(action).__name__}")
            return self._result(False, "Invalid action payload")

        action_type = action.get("action_type")
        if isinstance(action_type, str):
            action_type = action_type.strip()
        params = action.get("params") or {}
        if not isinstance(params, dict):
            logger.error(f"Invalid params type for action '{action_type}': {type(params).__name__}")
            params = {}
        reasoning = str(action.get("reasoning") or "No reasoning provided")
        if not isinstance(elements, list):
            elements = []

        if action_type in {"send_message", "message", "final_answer"}:
            action_type = "reply"

        self.log(f"Executing action: {action_type}")
        self.log(f"Reasoning: {reasoning}")

        if action_type == "reply":
            return self._execute_reply(params)

        if Config.should_ask_confirmation(self.agent.mode, reasoning):
            if self.agent.mode == OperationMode.GUIDE:
                self.log(f"[GUIDE MODE] Suggestion: {action_type} with {params}")
                return self._result(False, "Guide mode does not execute actions")
            elif self.agent.mode == OperationMode.SAFE or Config.is_dangerous_action(
                reasoning
            ):
                if self.agent.chat_window:
                    confirm = self.agent.chat_window.ask_confirmation(
                        "Action Review",
                        f"Action: {action_type}\nParams: {params}\n\nReason: {reasoning}\n\nExecute this?",
                    )
                else:
                    confirm_str = input(" Execute this action? (y/n): ").strip().lower()
                    confirm = confirm_str == "y"

                if not confirm:
                    self.log("Action cancelled by user")
                    return self._result(False, "Action cancelled by user")

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
            elif action_type == "switch_workspace":
                return self._execute_switch_workspace(params)
            elif action_type == "read_ui_text":
                return self._execute_read_ui_text(params)
            elif action_type == "list_windows":
                return self._execute_list_windows(params)
            elif action_type == "focus_window":
                return self._execute_focus_window(params)
            elif action_type == "sequence":
                return self._result(True, "Sequence delegated to orchestrator")
            else:
                logger.error(f"Unknown action type: {action_type}")
                return self._result(False, f"Unknown action type: {action_type}")
        except Exception as e:
            logger.error(f"Error executing action: {e}")
            return self._result(False, f"Error executing action: {e}")

    def _resolve_uia_rect(self, ui_element_id: str) -> Optional[Dict[str, int]]:
        target_id = str(ui_element_id or "").strip()
        if not target_id:
            return None

        snapshot = getattr(self.agent, "current_blind_snapshot", None) or {}
        for element in snapshot.get("elements", []):
            if element.get("ui_element_id") == target_id:
                rect = element.get("rect")
                if rect:
                    return rect

        return ui_automation.get_element_rect(
            self.agent.active_workspace,
            self.desktop_manager,
            target_id,
        )

    def _ensure_foreground_focus(self) -> None:
        try:
            ui_automation.ensure_foreground_focus(
                self.agent.active_workspace,
                self.desktop_manager,
            )
        except Exception:
            pass

    def _focus_uia_element(self, ui_element_id: str) -> Dict[str, Any]:
        return ui_automation.focus_element(
            self.agent.active_workspace,
            self.desktop_manager,
            ui_element_id,
        )

    def _execute_skill(self, params: Dict) -> Dict[str, Any]:
        skill_name = params.get("skill")
        method = params.get("method")
        args = params.get("args", {})

        self.log(f"Executing skill '{skill_name}' method '{method}'")

        if not skill_name:
            self.log("No skill name provided.")
            return self._result(False, "No skill name provided")

        skill = self.agent.skills.get(skill_name)
        if not skill:
            self.log(f"Unknown skill: {skill_name}")
            return self._result(False, f"Unknown skill: {skill_name}")

        result = skill.execute(method, args, desktop_manager=self.desktop_manager)
        self.log(f"{skill.name} Skill Result: {result}")

        if isinstance(result, str):
            lowered = result.strip().lower()
            if lowered.startswith(("error", "failed", "unknown", "no ")):
                return self._result(False, result)
            return self._result(True, result)
        return self._result(bool(result), f"Skill {skill_name}.{method} executed")

    def _execute_click(self, params: Dict, elements: List[Dict]) -> Dict[str, Any]:
        ui_element_id = str(params.get("ui_element_id") or "").strip()
        if ui_element_id:
            focus_result = self._focus_uia_element(ui_element_id)
            activation_result = ui_automation.activate_element(
                self.agent.active_workspace,
                self.desktop_manager,
                ui_element_id,
            )
            if activation_result.get("success"):
                time.sleep(Config.WAIT_AFTER_CLICK)
                return self._result(
                    True,
                    f"Activated UIA element {ui_element_id}",
                    payload={
                        "ui_element_id": ui_element_id,
                        "rect": activation_result.get("rect"),
                        "focus_method": focus_result.get("method"),
                        "activation_method": activation_result.get("method"),
                    },
                )

            rect = self._resolve_uia_rect(ui_element_id)
            if not rect:
                return self._result(False, f"UIA element not found: {ui_element_id}")

            final_x = int((int(rect["left"]) + int(rect["right"])) / 2)
            final_y = int((int(rect["top"]) + int(rect["bottom"])) / 2)
            if focus_result.get("success"):
                self.log(
                    f"Focused UIA target {ui_element_id} via "
                    f"{focus_result.get('method', 'focus')} before click"
                )
            self.log(f"Clicking UIA target {ui_element_id} at ({final_x}, {final_y})")

            clicked = mouse.click_at(final_x, final_y, desktop_manager=self.desktop_manager)
            if clicked is False:
                logger.error("UIA click operation reported failure.")
                return self._result(False, f"Failed to click UIA element {ui_element_id}")

            time.sleep(Config.WAIT_AFTER_CLICK)
            return self._result(
                True,
                f"Clicked UIA element {ui_element_id}",
                payload={
                    "ui_element_id": ui_element_id,
                    "rect": rect,
                    "focus_method": focus_result.get("method"),
                },
            )

        element_id = params.get("element_id")
        if element_id is None:
            element_id = params.get("target_id")
        if element_id is None:
            logger.error(f"Missing element_id in click params. Received: {params}")
            return self._result(False, "Missing element_id/ui_element_id in click params")
        try:
            element_id = int(element_id)
        except Exception:
            logger.error(f"Invalid element_id '{element_id}' in click params.")
            return self._result(False, f"Invalid element_id '{element_id}'")

        target = next((el for el in elements if el["id"] == element_id), None)
        if not target:
            logger.error(f"Element ID {element_id} not found")
            return self._result(False, f"Element ID {element_id} not found")

        if self.agent.is_magnified:
            full_w, full_h = pyautogui.size()

            norm_x = target["x"] / full_w
            norm_y = target["y"] / full_h

            crop_w = full_w / self.agent.zoom_level
            crop_h = full_h / self.agent.zoom_level

            real_x = self.agent.zoom_offset[0] + (norm_x * crop_w)
            real_y = self.agent.zoom_offset[1] + (norm_y * crop_h)
        else:
            real_x = target["x"]
            real_y = target["y"]

        scale_x, scale_y = self.agent.get_scale_factor()
        final_x = real_x * scale_x
        final_y = real_y * scale_y

        label = target.get("label", "unknown")
        self.log(f"Clicking ID {element_id} ('{label}') at ({final_x:.0f}, {final_y:.0f})")

        dm = self.desktop_manager
        clicked = mouse.click_at(int(final_x), int(final_y), desktop_manager=dm)
        if clicked is False:
            logger.error("Click operation reported failure.")
            return self._result(False, f"Failed to click visual element {element_id}")

        time.sleep(Config.WAIT_AFTER_CLICK)
        return self._result(
            True,
            f"Clicked visual element {element_id}",
            payload={"element_id": element_id, "label": label},
        )

    def _execute_type_text(self, params: Dict) -> Dict[str, Any]:
        text = params.get("text")
        if text is None:
            return self._result(False, "Missing text")
        text = str(text)
        if text == "":
            return self._result(False, "Text is empty")

        ui_element_id = str(params.get("ui_element_id") or "").strip()
        if ui_element_id:
            focus_result = self._focus_uia_element(ui_element_id)
            if not focus_result.get("success"):
                click_result = self._execute_click({"ui_element_id": ui_element_id}, [])
                if not click_result.get("success"):
                    return self._result(
                        False,
                        f"Failed to focus UIA element {ui_element_id}: {click_result.get('message')}",
                    )
            else:
                self.log(
                    f"Focused UIA target {ui_element_id} via "
                    f"{focus_result.get('method', 'focus')} before typing"
                )
        else:
            self._ensure_foreground_focus()

        dm = self.desktop_manager
        success = self.agent.keyboard.type_text(
            text, interval=Config.TYPING_INTERVAL, desktop_manager=dm
        )
        time.sleep(Config.WAIT_AFTER_TYPE)
        return self._result(
            success,
            f"Typed {len(text)} characters" if success else "Failed to type text",
            payload={"text": text, "ui_element_id": ui_element_id or None},
        )

    def _execute_press_key(self, params: Dict) -> Dict[str, Any]:
        key = params.get("key")
        if not key:
            return self._result(False, "Missing key")
        if not isinstance(key, str):
            return self._result(False, "Key must be a string")

        self._ensure_foreground_focus()
        dm = self.desktop_manager
        success = self.agent.keyboard.press_key(key, desktop_manager=dm)
        time.sleep(Config.WAIT_AFTER_TYPE)
        return self._result(success, f"Pressed key: {key}" if success else f"Failed to press key: {key}")

    def _execute_key_combo(self, params: Dict) -> Dict[str, Any]:
        keys = params.get("keys")
        if not keys:
            return self._result(False, "Missing keys")
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split("+") if k.strip()]
        if not isinstance(keys, (list, tuple)):
            return self._result(False, "Keys must be a list or '+'-joined string")

        self._ensure_foreground_focus()
        dm = self.desktop_manager
        success = self.agent.keyboard.key_combo(*keys, desktop_manager=dm)
        time.sleep(Config.WAIT_AFTER_TYPE)
        combo = "+".join(keys)
        return self._result(success, f"Pressed combo: {combo}" if success else f"Failed combo: {combo}")

    def _execute_wait(self, params: Dict) -> Dict[str, Any]:
        seconds = params.get("seconds", 1.0)
        try:
            seconds = float(seconds)
            if seconds < 0:
                return self._result(False, "Wait seconds must be non-negative")
        except Exception:
            return self._result(False, "Invalid wait seconds")
        self.log(f"Waiting for {seconds} seconds...")
        time.sleep(seconds)
        return self._result(True, f"Waited for {seconds} seconds")

    def _execute_search_web(self, params: Dict) -> Dict[str, Any]:
        query = params.get("query")
        if not query:
            return self._result(False, "Missing search query")

        self.log(f"Searching web for: {query}")
        dm = self.desktop_manager
        result = self.agent.browser_skill.search(query, desktop_manager=dm)
        if isinstance(result, str):
            lowered = result.strip().lower()
            if lowered.startswith(("error", "failed", "no ")):
                self.log(result)
                return self._result(False, result)
            return self._result(True, result)
        return self._result(bool(result), f"Searched web for: {query}")

    def _execute_open_app(self, params: Dict) -> Dict[str, Any]:
        app_name = params.get("app_name")
        if not app_name:
            return self._result(False, "Missing app_name")
        app_name = str(app_name).strip()
        if not app_name:
            return self._result(False, "app_name is empty")

        self.log(f"Opening app: {app_name}")
        dm = self.desktop_manager
        existing_focus = ui_automation.focus_window(
            self.agent.active_workspace,
            dm,
            title_contains=app_name,
            process_name=app_name,
            restore=True,
            maximize=False,
        )
        if existing_focus.get("success"):
            return self._result(
                True,
                f"Focused existing app window: {app_name}",
                payload={"window": existing_focus.get("window")},
            )

        if self.agent.app_indexer.open_app(app_name, desktop_manager=dm):
            time.sleep(Config.APP_LAUNCH_WAIT)
            launched_focus = ui_automation.focus_window(
                self.agent.active_workspace,
                dm,
                title_contains=app_name,
                process_name=app_name,
                restore=True,
                maximize=False,
            )
            if launched_focus.get("success"):
                return self._result(
                    True,
                    f"Opened app and verified window: {app_name}",
                    payload={"window": launched_focus.get("window")},
                )
            return self._result(False, f"App launched but window not verified: {app_name}")

        start_ok = self.agent.keyboard.press_key("win", desktop_manager=dm)
        time.sleep(1.0)
        type_ok = self.agent.keyboard.type_text(app_name, desktop_manager=dm)
        time.sleep(0.8)
        enter_ok = self.agent.keyboard.press_key("enter", desktop_manager=dm)

        time.sleep(Config.APP_LAUNCH_WAIT)
        shortcut_ok = bool(start_ok and type_ok and enter_ok)
        if not shortcut_ok:
            return self._result(False, f"Failed to open app: {app_name}")

        launched_focus = ui_automation.focus_window(
            self.agent.active_workspace,
            dm,
            title_contains=app_name,
            process_name=app_name,
            restore=True,
            maximize=False,
        )
        if launched_focus.get("success"):
            return self._result(
                True,
                f"Opened app and verified window: {app_name}",
                payload={"window": launched_focus.get("window")},
            )
        return self._result(False, f"App launch attempted but window not verified: {app_name}")

    def _execute_magnify(self, params: Dict, elements: List[Dict]) -> Dict[str, Any]:
        element_id = params.get("element_id")
        zoom = params.get("zoom_level", 2.0)

        target = next((el for el in elements if el["id"] == element_id), None)
        if not target:
            logger.error("Magnify target not found")
            return self._result(False, "Magnify target not found")

        self.log(f"Magnifying ID {element_id} at {zoom}x")
        self.agent.is_magnified = True
        self.agent.zoom_level = zoom
        self.agent.zoom_center = (target["x"], target["y"])
        return self._result(True, f"Magnifying element {element_id}", payload={"element_id": element_id, "zoom_level": zoom})

    def _execute_reply(self, params: Dict) -> Dict[str, Any]:
        text = params.get("text")
        if not text:
            text = params.get("message") or params.get("content")
        if not text:
            return self._result(False, "Reply text missing")

        self.log(f"Reply: {text}")
        self.agent.deferred_reply = text
        return self._result(True, "Queued reply", payload={"text": text})

    def _execute_switch_workspace(self, params: Dict) -> Dict[str, Any]:
        workspace = params.get("workspace")
        if not workspace:
            return self._result(False, "Missing workspace")

        self.log(f"Switching to workspace: {workspace}")
        self.agent._set_workspace(workspace, reason="Agent requested switch")
        return self._result(True, f"Switched workspace to {workspace}", payload={"workspace": workspace})

    def _execute_list_windows(self, params: Dict) -> Dict[str, Any]:
        result = ui_automation.list_windows(
            self.agent.active_workspace,
            self.desktop_manager,
            title_contains=str(params.get("title_contains") or ""),
            process_name=str(params.get("process_name") or ""),
            visible_only=bool(params.get("visible_only", False)),
            max_windows=int(params.get("max_windows") or Config.UIA_MAX_WINDOWS),
        )
        if result.get("status") == "ok":
            return self._result(
                True,
                f"Listed {result.get('windows_count', 0)} window(s)",
                payload=result,
            )
        return self._result(
            False,
            f"Failed to list windows: {result.get('reason', 'unknown')}",
            payload=result,
        )

    def _execute_focus_window(self, params: Dict) -> Dict[str, Any]:
        result = ui_automation.focus_window(
            self.agent.active_workspace,
            self.desktop_manager,
            window_id=str(params.get("window_id") or "").strip() or None,
            title_contains=str(params.get("title_contains") or ""),
            process_name=str(params.get("process_name") or ""),
            restore=bool(params.get("restore", True)),
            maximize=bool(params.get("maximize", False)),
        )
        if result.get("success"):
            window = result.get("window") or {}
            title = str(window.get("title") or result.get("window_id") or "target")
            return self._result(True, f"Focused window: {title}", payload=result)
        return self._result(
            False,
            f"Failed to focus window: {result.get('reason', 'unknown')}",
            payload=result,
        )

    def _execute_read_ui_text(self, params: Dict) -> Dict[str, Any]:
        result = ui_automation.read_text(
            self.agent.active_workspace,
            self.desktop_manager,
            target=str(params.get("target") or "auto"),
            ui_element_id=str(params.get("ui_element_id") or "").strip() or None,
            max_chars=int(params.get("max_chars") or Config.UIA_TEXT_MAX_CHARS),
            use_ocr_fallback=bool(
                params.get("use_ocr_fallback", Config.UIA_TEXT_USE_OCR_FALLBACK_DEFAULT)
            ),
            force_ocr=bool(params.get("force_ocr", False)),
            ocr_min_chars=int(params.get("ocr_min_chars") or Config.UIA_TEXT_OCR_MIN_CHARS),
            ocr_max_noise_ratio=float(
                params.get("ocr_max_noise_ratio") or Config.UIA_TEXT_OCR_MAX_NOISE_RATIO
            ),
        )

        text = str(result.get("text") or "")
        if result.get("status") == "ok" and text:
            message = (
                f"Read UI text from {result.get('seed_source') or result.get('target')}: "
                f"{text[:120]}"
            )
            return self._result(True, message, payload=result)

        return self._result(
            False,
            f"Failed to read UI text: {result.get('reason', 'unknown')}",
            payload=result,
        )
