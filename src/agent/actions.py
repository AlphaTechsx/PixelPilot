import time
import logging
import sys
import pyautogui
import tools.mouse as mouse
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

    @property
    def desktop_manager(self):
        if self.agent.active_workspace == "agent":
            return self.agent.desktop_manager
        return None

    def execute(self, action: Dict[str, Any], elements: List[Dict]) -> bool:
        """
        Dispatch method for executing actions.
        """
        action_type = action.get("action_type")
        params = action.get("params", {})

        self.log(f"Executing action: {action_type}")
        self.log(f"Reasoning: {action['reasoning']}")

        if action_type == "reply":
            return self._execute_reply(params)

        if Config.should_ask_confirmation(self.agent.mode, action["reasoning"]):
            if self.agent.mode == OperationMode.GUIDE:
                self.log(f"[GUIDE MODE] Suggestion: {action_type} with {params}")
                return False
            elif self.agent.mode == OperationMode.SAFE or Config.is_dangerous_action(
                action["reasoning"]
            ):
                if self.agent.chat_window:
                    confirm = self.agent.chat_window.ask_confirmation(
                        "Action Review",
                        f"Action: {action_type}\nParams: {params}\n\nReason: {action['reasoning']}\n\nExecute this?",
                    )
                else:
                    confirm_str = input(" Execute this action? (y/n): ").strip().lower()
                    confirm = confirm_str == "y"

                if not confirm:
                    self.log("Action cancelled by user")
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
            elif action_type == "switch_workspace":
                return self._execute_switch_workspace(params)
            elif action_type == "sequence":
                logger.warning(
                    "sequence type passed to execute_action. This should be handled in run_task."
                )
                return False
            else:
                logger.error(f"Unknown action type: {action_type}")
                return False
        except Exception as e:
            logger.error(f"Error executing action: {e}")
            return False

    def _execute_skill(self, params: Dict) -> bool:
        skill_name = params.get("skill")
        method = params.get("method")
        args = params.get("args", {})

        self.log(f"Executing skill '{skill_name}' method '{method}'")

        if not skill_name:
            self.log("No skill name provided.")
            return False

        skill = self.agent.skills.get(skill_name)
        if not skill:
            self.log(f"Unknown skill: {skill_name}")
            return False

        result = skill.execute(method, args, desktop_manager=self.desktop_manager)
        self.log(f"{skill.name} Skill Result: {result}")
        return True

    def _execute_click(self, params: Dict, elements: List[Dict]) -> bool:
        element_id = params.get("element_id") or params.get("target_id")
        if element_id is None:
            logger.error(f"Missing element_id in click params. Received: {params}")
            return False

        target = next((el for el in elements if el["id"] == element_id), None)
        if not target:
            logger.error(f"Element ID {element_id} not found")
            return False

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

        self.log(
            f"Clicking ID {element_id} ('{target['label']}') at ({final_x:.0f}, {final_y:.0f})"
        )

        dm = self.desktop_manager
        mouse.click_at(int(final_x), int(final_y), desktop_manager=dm)

        time.sleep(Config.WAIT_AFTER_CLICK)
        return True

    def _execute_type_text(self, params: Dict) -> bool:
        text = params.get("text")
        if not text:
            return False

        dm = self.desktop_manager
        success = self.agent.keyboard.type_text(
            text, interval=Config.TYPING_INTERVAL, desktop_manager=dm
        )
        time.sleep(Config.WAIT_AFTER_TYPE)
        return success

    def _execute_press_key(self, params: Dict) -> bool:
        key = params.get("key")
        if not key:
            return False

        dm = self.desktop_manager
        success = self.agent.keyboard.press_key(key, desktop_manager=dm)
        time.sleep(Config.WAIT_AFTER_TYPE)
        return success

    def _execute_key_combo(self, params: Dict) -> bool:
        keys = params.get("keys")
        if not keys:
            return False

        dm = self.desktop_manager
        success = self.agent.keyboard.hotkey(*keys, desktop_manager=dm)
        time.sleep(Config.WAIT_AFTER_TYPE)
        return success

    def _execute_wait(self, params: Dict) -> bool:
        seconds = params.get("seconds", 1.0)
        self.log(f"Waiting for {seconds} seconds...")
        time.sleep(seconds)
        return True

    def _execute_search_web(self, params: Dict) -> bool:
        query = params.get("query")
        if not query:
            return False

        self.log(f"Searching web for: {query}")
        dm = self.desktop_manager
        return self.agent.browser_skill.search(query, desktop_manager=dm)

    def _execute_open_app(self, params: Dict) -> bool:
        app_name = params.get("app_name")
        if not app_name:
            return False

        self.log(f"Opening app: {app_name}")
        dm = self.desktop_manager

        if self.agent.app_indexer.open_app(app_name, desktop_manager=dm):
            time.sleep(Config.APP_LAUNCH_WAIT)
            return True

        self.agent.keyboard.press_key("win", desktop_manager=dm)
        time.sleep(1.0)
        self.agent.keyboard.type_text(app_name, desktop_manager=dm)
        time.sleep(0.8)
        self.agent.keyboard.press_key("enter", desktop_manager=dm)

        time.sleep(Config.APP_LAUNCH_WAIT)
        return True

    def _execute_magnify(self, params: Dict, elements: List[Dict]) -> bool:
        element_id = params.get("element_id")
        zoom = params.get("zoom_level", 2.0)

        target = next((el for el in elements if el["id"] == element_id), None)
        if not target:
            logger.error("Magnify target not found")
            return False

        self.log(f"Magnifying ID {element_id} at {zoom}x")
        self.agent.is_magnified = True
        self.agent.zoom_level = zoom
        self.agent.zoom_center = (target["x"], target["y"])
        return True

    def _execute_reply(self, params: Dict) -> bool:
        text = params.get("text")
        if not text:
            return False

        self.log(f"Reply: {text}")
        if self.agent.chat_window:
            try:
                self.agent.chat_window.add_final_answer(text)
            except Exception as e:
                logger.error(f"Failed to add final answer to chat: {e}")
        return True

    def _execute_switch_workspace(self, params: Dict) -> bool:
        workspace = params.get("workspace")
        if not workspace:
            return False

        self.log(f"Switching to workspace: {workspace}")
        self.agent._set_workspace(workspace, reason="Agent requested switch")
        return True
