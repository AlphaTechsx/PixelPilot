"""
Interactive Guidance Mode - Step-by-step tutorial system.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
from pydantic import BaseModel, Field

from agent.brain import get_model
from agent.guidance_prompts import (
    INSTRUCTION_PROMPT,
    CLARIFICATION_PROMPT,
    VERIFICATION_PROMPT,
    INTENT_PROMPT,
    GOAL_COMPLETE_PROMPT,
)
from config import Config

logger = logging.getLogger(__name__)


class VerificationResult(BaseModel):
    """Result of step verification."""
    success: bool = Field(description="Whether the step was completed successfully")
    confidence: float = Field(description="Confidence score 0.0-1.0")
    observation: str = Field(description="What was observed on screen")
    suggestion: Optional[str] = Field(default=None, description="Suggestion if step failed")


class GoalStatus(BaseModel):
    """Status of the overall goal."""
    complete: bool = Field(description="Whether the goal has been achieved")
    confidence: float = Field(description="Confidence score 0.0-1.0")
    reason: str = Field(description="Explanation of the status")


class GuidanceSession:
    """
    Interactive tutorial session that guides users step-by-step.
    
    The AI watches the screen, gives instructions, and the user performs
    actions themselves. Supports conversational clarification mid-step.
    """
    
    def __init__(
        self,
        user_goal: str,
        chat_window,
        capture_func,
        stop_check_func,
    ):
        """
        Initialize a guidance session.
        
        Args:
            user_goal: What the user wants to accomplish
            chat_window: GUI adapter for communication
            capture_func: Function that captures screen and returns (elements, screenshot_path)
            stop_check_func: Function to check if user requested stop
        """
        self.user_goal = user_goal
        self.chat_window = chat_window
        self.capture_func = capture_func
        self.stop_check = stop_check_func
        
        self.model = get_model()
        self.completed_steps: List[str] = []
        self.current_instruction: Optional[str] = None
        self.step_count = 0
        self.max_steps = 50
        self._active = False
        
        self._last_elements: List[Dict] = []
        self._last_screenshot_path: Optional[str] = None
    
    def run(self) -> bool:
        """
        Run the guidance session until goal is complete or user stops.
        
        Returns:
            True if goal was completed, False otherwise.
        """
        self._active = True
        self._log(f"Starting guidance session for: {self.user_goal}")
        
        if self.chat_window:
            self.chat_window.add_activity_message(f"Starting guidance for: {self.user_goal}")
        
        try:
            self.stop_check()
            elements, screenshot_path = self._capture_screen()
            
            if not elements and not screenshot_path:
                self._send_message("I'm having trouble seeing your screen. Please try again.")
                return False
            
            instruction = self._generate_instruction(elements)
            self._send_message(instruction)
            self.current_instruction = instruction
            
            while self._active and self.step_count < self.max_steps:
                self.stop_check()
                
                user_message, proceed = self._wait_for_user()
                
                if not proceed:
                    self._log("User cancelled or session ended")
                    break
                
                if not user_message:
                    continue
                
                result = self._handle_user_message(user_message)
                
                if result == "complete":
                    self._active = False
                    return True
                elif result == "stopped":
                    self._active = False
                    return False
            
            if self.step_count >= self.max_steps:
                self._send_message("We've reached the step limit. Let's start fresh if you need more help!")
            
            return False
            
        except Exception as e:
            logger.exception("Error in guidance session")
            self._send_message(f"Sorry, I encountered an error: {str(e)}")
            return False
        finally:
            self._active = False
    
    def _handle_user_message(self, message: str) -> str:
        """
        Handle a user message and determine next action.
        
        Returns:
            "continue" - keep going
            "complete" - goal is done
            "stopped" - user wants to stop
        """
        intent = self._classify_intent(message)
        self._log(f"User intent: {intent} (message: {message[:50]}...)")
        
        if intent == "stop":
            self._send_message("No problem! Let me know if you need help with anything else.")
            return "stopped"
        
        if intent == "repeat":
            if self.current_instruction:
                self._send_message(f"Sure! Here's what to do: {self.current_instruction}")
            else:
                self._send_message("Let me check the screen and give you a fresh instruction...")
                self._give_next_instruction()
            return "continue"
        
        if intent == "question":
            answer = self._answer_question(message)
            self._send_message(answer)
            return "continue"
        
        if intent == "problem":
            self.stop_check()
            elements, _ = self._capture_screen()
            
            problem_response = self._handle_problem(message, elements)
            self._send_message(problem_response)
            return "continue"
        
        if intent == "next":
            self.stop_check()
            self.step_count += 1
            
            elements, screenshot_path = self._capture_screen()
            
            if not elements and not screenshot_path:
                self._send_message("I'm having trouble seeing your screen. Did the action work? If yes, tell me what happened.")
                return "continue"
            
            goal_status = self._check_goal_complete(elements)
            
            if goal_status and goal_status.complete and goal_status.confidence > 0.7:
                self.completed_steps.append(self.current_instruction or "Previous step")
                self._send_message(f"🎉 {goal_status.reason}")
                self._wait_for_user_ack("Done")
                return "complete"
            
            if self.current_instruction:
                verification = self._verify_step(elements)
                
                if verification and verification.success:
                    self.completed_steps.append(self.current_instruction)
                    self._log(f"Step verified: {verification.observation}")
                elif verification and not verification.success:
                    self._log(f"Step failed: {verification.observation}")
                    if verification.suggestion:
                        self._send_message(f"Hmm, {verification.observation}. {verification.suggestion}")
                        return "continue"
            
            self._give_next_instruction()
            return "continue"
        
        return self._handle_user_message("next")
    
    def _give_next_instruction(self):
        """Capture screen and give the next instruction."""
        self.stop_check()
        elements, _ = self._capture_screen()
        
        if not elements:
            self._send_message("I'm having trouble seeing your screen. Try moving any windows and say 'next' to continue.")
            return
        
        instruction = self._generate_instruction(elements)
        self._send_message(instruction)
        self.current_instruction = instruction
    
    def _capture_screen(self) -> Tuple[List[Dict], Optional[str]]:
        """Capture the screen and return elements."""
        try:
            elements, _ = self.capture_func()
            self._last_elements = elements or []
            self._last_screenshot_path = Config.SCREENSHOT_PATH
            return self._last_elements, self._last_screenshot_path
        except Exception as e:
            logger.exception("Screen capture failed")
            return [], None
    
    def _generate_instruction(self, elements: List[Dict]) -> str:
        """Generate a conversational instruction based on screen state."""
        elements_desc = self._format_elements(elements)
        completed_desc = self._format_completed_steps()
        
        prompt = INSTRUCTION_PROMPT.format(
            user_goal=self.user_goal,
            completed_steps=completed_desc or "None yet - this is the first step!",
            elements_description=elements_desc,
        )
        
        try:
            contents = [prompt]
            if self._last_screenshot_path:
                try:
                    img = Image.open(self._last_screenshot_path)
                    contents.append(img)
                except Exception:
                    pass
            
            response = self.model.generate_content(contents)
            instruction = response.text.strip()
            
            instruction = instruction.replace("**", "").strip()
            
            return instruction
            
        except Exception as e:
            logger.exception("Failed to generate instruction")
            return "I'm having trouble thinking of the next step. Can you describe what you see on screen?"
    
    def _classify_intent(self, message: str) -> str:
        """Classify user message intent."""
        msg_lower = message.lower().strip()
        
        if msg_lower in ("done", "next", "ok", "okay", "yes", "yep", "continue", "go"):
            return "next"
        if msg_lower in ("stop", "cancel", "quit", "exit", "nevermind", "never mind"):
            return "stop"
        if msg_lower in ("repeat", "again", "what?", "huh?"):
            return "repeat"
        if "?" in message:
            return "question"
        
        prompt = INTENT_PROMPT.format(user_message=message)
        
        try:
            response = self.model.generate_content([prompt])
            intent = response.text.strip().lower()
            
            if intent in ("next", "question", "problem", "stop", "repeat"):
                return intent
            return "next"  
        except Exception:
            return "next"  
    
    def _answer_question(self, question: str) -> str:
        """Answer a clarification question from the user."""
        elements_desc = self._format_elements(self._last_elements)
        
        prompt = CLARIFICATION_PROMPT.format(
            user_goal=self.user_goal,
            current_instruction=self.current_instruction or "Getting started",
            user_question=question,
            elements_description=elements_desc,
        )
        
        try:
            contents = [prompt]
            if self._last_screenshot_path:
                try:
                    img = Image.open(self._last_screenshot_path)
                    contents.append(img)
                except Exception:
                    pass
            
            response = self.model.generate_content(contents)
            return response.text.strip()
            
        except Exception as e:
            logger.exception("Failed to answer question")
            return "I'm not sure about that. Let me give you the instruction again in a different way..."
    
    def _handle_problem(self, problem_description: str, elements: List[Dict]) -> str:
        """Handle when user reports a problem."""
        elements_desc = self._format_elements(elements)
        
        prompt = f"""The user is trying to: {self.user_goal}

The instruction was: {self.current_instruction or 'Getting started'}

They reported a problem: "{problem_description}"

Current screen shows:
{elements_desc}

Give them helpful troubleshooting advice or an alternative approach. Be encouraging and specific."""
        
        try:
            contents = [prompt]
            if self._last_screenshot_path:
                try:
                    img = Image.open(self._last_screenshot_path)
                    contents.append(img)
                except Exception:
                    pass
            
            response = self.model.generate_content(contents)
            return response.text.strip()
            
        except Exception:
            return "I see there's an issue. Can you tell me more about what happened? Or we can try a different approach."
    
    def _verify_step(self, elements: List[Dict]) -> Optional[VerificationResult]:
        """Verify that the previous step was completed."""
        if not self.current_instruction:
            return VerificationResult(
                success=True,
                confidence=0.5,
                observation="No specific step to verify",
                suggestion=None
            )
        
        elements_desc = self._format_elements(elements)
        
        prompt = VERIFICATION_PROMPT.format(
            user_goal=self.user_goal,
            step_description=self.current_instruction,
            expected_change="The instruction was followed successfully",
            elements_description=elements_desc,
        )
        
        try:
            contents = [prompt]
            if self._last_screenshot_path:
                try:
                    img = Image.open(self._last_screenshot_path)
                    contents.append(img)
                except Exception:
                    pass
            
            response = self.model.generate_content(
                contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": VerificationResult.model_json_schema(),
                },
            )
            
            return VerificationResult.model_validate_json(response.text)
            
        except Exception as e:
            logger.exception("Verification failed")
            # Assume success on verification failure
            return VerificationResult(
                success=True,
                confidence=0.3,
                observation="Could not verify (assuming success)",
                suggestion=None
            )
    
    def _check_goal_complete(self, elements: List[Dict]) -> Optional[GoalStatus]:
        """Check if the overall goal has been completed."""
        elements_desc = self._format_elements(elements)
        
        prompt = GOAL_COMPLETE_PROMPT.format(
            user_goal=self.user_goal,
            elements_description=elements_desc,
        )
        
        try:
            contents = [prompt]
            if self._last_screenshot_path:
                try:
                    img = Image.open(self._last_screenshot_path)
                    contents.append(img)
                except Exception:
                    pass
            
            response = self.model.generate_content(
                contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": GoalStatus.model_json_schema(),
                },
            )
            
            return GoalStatus.model_validate_json(response.text)
            
        except Exception:
            return None
    
    def _wait_for_user(self) -> Tuple[Optional[str], bool]:
        """
        Wait for user input.
        
        Returns:
            (message, should_proceed) - message is None if cancelled
        """
        if not self.chat_window:
            # CLI fallback
            try:
                msg = input("Your response (or 'stop' to cancel): ").strip()
                return msg, bool(msg)
            except (EOFError, KeyboardInterrupt):
                return None, False
        
        # GUI mode - wait for guidance response
        import threading
        
        payload = {
            "result": False,
            "event": threading.Event(),
            "feedback": None,
            "label": "Next",
            "final": False,
            "steps": {
                "done": list(self.completed_steps),
                "current": self.current_instruction,
            },
        }
        
        # Request user input via GUI
        self.chat_window.request_guidance_input(payload)
        
        while True:
            self.stop_check()
            if payload["event"].wait(0.2):
                if payload.get("cancelled"):
                    return None, False
                return payload.get("feedback"), True
            time.sleep(0.05)

    def _wait_for_user_ack(self, label: str = "Done") -> None:
        if not self.chat_window:
            return

        import threading

        payload = {
            "event": threading.Event(),
            "feedback": None,
            "label": label,
            "final": True,
            "steps": {
                "done": list(self.completed_steps),
                "current": None,
            },
        }

        self.chat_window.request_guidance_input(payload)

        while True:
            self.stop_check()
            if payload["event"].wait(0.2):
                return
            time.sleep(0.05)
    
    def _send_message(self, message: str):
        """Send a message to the user."""
        if self.chat_window:
            self.chat_window.add_final_answer(message)
        else:
            print(f"\n🤖 {message}\n")
    
    def _log(self, message: str):
        """Log a message."""
        logger.info(f"[Guidance] {message}")
        if self.chat_window:
            self.chat_window.add_activity_message(f"[Guidance] {message}")
    
    def _format_elements(self, elements: List[Dict], max_elements: int = 80) -> str:
        """Format detected elements for prompt."""
        if not elements:
            return "No UI elements detected"
        
        lines = []
        for el in elements[:max_elements]:
            el_type = el.get("type", "element")
            label = el.get("label", "")
            x, y = el.get("x", 0), el.get("y", 0)
            
            # Add position hint
            pos = self._position_hint(x, y)
            
            if label:
                lines.append(f"- {el_type}: '{label}' ({pos})")
            else:
                lines.append(f"- {el_type} ({pos})")
        
        if len(elements) > max_elements:
            lines.append(f"... and {len(elements) - max_elements} more elements")
        
        return "\n".join(lines)
    
    def _format_completed_steps(self) -> str:
        """Format completed steps for context."""
        if not self.completed_steps:
            return ""
        
        return "\n".join([f"{i+1}. {step}" for i, step in enumerate(self.completed_steps)])
    
    def _position_hint(self, x: float, y: float) -> str:
        """Get a human-readable position hint."""
        # Assume 1920x1080 as default
        width, height = 1920, 1080
        
        x_ratio = x / float(width) if width else 0.5
        y_ratio = y / float(height) if height else 0.5
        
        if x_ratio < 0.33:
            horiz = "left"
        elif x_ratio > 0.66:
            horiz = "right"
        else:
            horiz = "center"
        
        if y_ratio < 0.33:
            vert = "top"
        elif y_ratio > 0.66:
            vert = "bottom"
        else:
            vert = "middle"
        
        if horiz == "center" and vert == "middle":
            return "center"
        
        return f"{vert}-{horiz}"


def create_guidance_session(
    user_goal: str,
    chat_window,
    capture_func,
    stop_check_func,
) -> GuidanceSession:
    """
    Create a new guidance session.
    
    Args:
        user_goal: What the user wants to accomplish
        chat_window: GUI adapter for communication
        capture_func: Function that captures screen and returns (elements, reference_sheet)
        stop_check_func: Function to check if user requested stop
    
    Returns:
        GuidanceSession instance
    """
    return GuidanceSession(
        user_goal=user_goal,
        chat_window=chat_window,
        capture_func=capture_func,
        stop_check_func=stop_check_func,
    )
