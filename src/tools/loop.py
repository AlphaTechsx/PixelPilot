import json
import logging
import hashlib
from collections import deque
import imagehash
from typing import Any, Dict, List, Optional
from PIL import Image
from agent.brain import get_model
from agent.prompts import LOOP_DEBUG_PROMPT


class LoopDetector:
    """
    Detects when the AI agent is stuck in a loop repeating the same actions.
    """
    
    logger = logging.getLogger("pixelpilot.loop")

    def __init__(self, threshold: int = 3, similarity_threshold: float = 0.95):
        """
        Initialize the loop detector.

        Args:
            threshold: Number of similar actions before flagging as loop
            similarity_threshold: How similar screens must be (0-1) to consider them identical
        """
        self.threshold = threshold
        self.similarity_threshold = similarity_threshold

        self.action_history: deque = deque(maxlen=10)

        self.screen_hashes: deque = deque(maxlen=10)

        self.loop_detected = False
        self.loop_info: Optional[Dict] = None

    def hash_screen(self, screenshot_path: str) -> str:
        """
        Create a perceptual hash of the screenshot for comparison.
        Uses pHash for efficient similarity detection.

        Args:
            screenshot_path: Path to screenshot file

        Returns:
            Hash string representing the screen state
        """
        try:
            img = Image.open(screenshot_path)

            phash = imagehash.phash(img, hash_size=16)
            return str(phash)
        except Exception as e:
            self.logger.warning(f"Warning: Could not hash screen: {e}")

            with open(screenshot_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()

    def _hash_action(self, action: Dict[str, Any]) -> str:
        """
        Create a hash of an action for comparison.

        Args:
            action: Action dictionary

        Returns:
            Hash string
        """

        action_str = f"{action.get('action_type', '')}_{str(action.get('params', {}))}"
        return hashlib.md5(action_str.encode()).hexdigest()

    def _compare_hashes(self, hash1: str, hash2: str) -> float:
        """
        Compare two perceptual hashes and return similarity score.

        Args:
            hash1: First hash
            hash2: Second hash

        Returns:
            Similarity score (0.0 = completely different, 1.0 = identical)
        """
        try:
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)

            max_distance = len(hash1) * 4
            distance = h1 - h2
            similarity = 1.0 - (distance / max_distance)
            return similarity
        except Exception:
            return 1.0 if hash1 == hash2 else 0.0

    def track_action(self, action: Dict[str, Any], screen_hash: str) -> bool:
        """
        Track an action and check if we're in a loop.

        Args:
            action: The action being executed
            screen_hash: Hash of the current screen state

        Returns:
            True if loop detected, False otherwise
        """
        action_hash = self._hash_action(action)

        self.action_history.append(
            {"action_hash": action_hash, "action": action, "screen_hash": screen_hash}
        )
        self.screen_hashes.append(screen_hash)

        if len(self.action_history) < self.threshold:
            return False

        recent_actions = list(self.action_history)[-self.threshold :]

        action_types = [a["action"]["action_type"] for a in recent_actions]
        if len(set(action_types)) == 1:
            recent_screens = [a["screen_hash"] for a in recent_actions]

            all_similar = True
            for i in range(len(recent_screens) - 1):
                similarity = self._compare_hashes(
                    recent_screens[i], recent_screens[i + 1]
                )
                if similarity < self.similarity_threshold:
                    all_similar = False
                    break

            if all_similar:
                self.loop_detected = True
                self.loop_info = {
                    "pattern": "repeated_action",
                    "action_type": action_types[0],
                    "count": self.threshold,
                    "actions": [a["action"] for a in recent_actions],
                }
                return True

        if len(self.action_history) >= self.threshold + 2:
            recent_extended = list(self.action_history)[-(self.threshold + 2) :]
            action_hashes_extended = [a["action_hash"] for a in recent_extended]
            unique_actions = set(action_hashes_extended)

            if len(unique_actions) <= 3:
                screen_hashes_extended = [a["screen_hash"] for a in recent_extended]
                avg_similarity = sum(
                    self._compare_hashes(
                        screen_hashes_extended[i], screen_hashes_extended[0]
                    )
                    for i in range(1, len(screen_hashes_extended))
                ) / (len(screen_hashes_extended) - 1)

                if avg_similarity >= self.similarity_threshold:
                    self.loop_detected = True
                    self.loop_info = {
                        "pattern": "alternating_actions",
                        "unique_actions": len(unique_actions),
                        "count": len(recent_extended),
                        "actions": [a["action"] for a in recent_extended],
                    }
                    return True

        if len(self.action_history) >= self.threshold + 3:
            recent_stall = list(self.action_history)[-(self.threshold + 3) :]
            screen_hashes_stall = [a["screen_hash"] for a in recent_stall]

            stall_count = 0
            for i in range(len(screen_hashes_stall) - 1):
                if (
                    self._compare_hashes(
                        screen_hashes_stall[i], screen_hashes_stall[i + 1]
                    )
                    >= self.similarity_threshold
                ):
                    stall_count += 1

            if stall_count >= self.threshold:
                self.loop_detected = True
                self.loop_info = {
                    "pattern": "interaction_stall",
                    "count": len(recent_stall),
                    "reason": "Multiple actions taken but screen state remains stagnant",
                }
                return True

        return False

    def get_loop_info(self) -> Optional[Dict]:
        """
        Get information about the detected loop.

        Returns:
            Dictionary with loop details or None if no loop detected
        """
        return self.loop_info

    def suggest_alternatives(
        self, user_command: str, current_action: Dict[str, Any]
    ) -> List[str]:
        """
        Use AI to suggest alternative approaches when stuck in a loop.

        Args:
            user_command: Original user command
            current_action: The action that's being repeated

        Returns:
            List of suggested alternative actions
        """
        if not self.loop_info:
            return []

        try:
            prompt = LOOP_DEBUG_PROMPT.format(
                user_command=user_command,
                pattern=self.loop_info.get("pattern"),
                action_type=current_action.get("action_type"),
                params=current_action.get("params"),
                reasoning=current_action.get("reasoning"),
                count=self.loop_info.get("count", 0),
            )

            model = get_model()
            response = model.generate_content(
                [prompt], config={"response_mime_type": "application/json"}
            )

            # The backend client returns a dict {"text": "..."}
            if isinstance(response, dict):
                text_content = response.get("text", "{}")
            else:
                # Fallback if it's somehow an object
                text_content = getattr(response, "text", "{}")

            result = json.loads(text_content)
            return result.get("suggestions", [])

        except Exception as e:
            self.logger.error(f"Error generating loop alternatives: {e}")

            return [
                "Try a different approach to accomplish the same goal",
                "Use keyboard shortcuts instead of clicking",
                "Ask the user for clarification on how to proceed",
            ]

    def clear(self):
        """Reset the loop detection state."""
        self.action_history.clear()
        self.screen_hashes.clear()
        self.loop_detected = False
        self.loop_info = None

    def __repr__(self):
        return (
            f"LoopDetector(threshold={self.threshold}, detected={self.loop_detected})"
        )
