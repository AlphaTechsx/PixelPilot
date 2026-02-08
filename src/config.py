import os
from pathlib import Path
from enum import Enum
from dotenv import load_dotenv

load_dotenv()


class OperationMode(Enum):
    GUIDE = "guide"
    SAFE = "safe"
    AUTO = "auto"


class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

    DEFAULT_MODE = OperationMode(os.getenv("DEFAULT_MODE", OperationMode.AUTO.value))
    VISION_MODE = os.getenv("VISION_MODE", "robo").strip().lower()

    USE_ROBOTICS_EYE = VISION_MODE in {"robo", "robotics", "er", "robotics-er"}
    ROBOTICS_USE_BOUNDING_BOXES = True

    LAZY_VISION = not USE_ROBOTICS_EYE
    INCREMENTAL_SCREENSHOTS = True
    ROBOTICS_MAX_ELEMENTS = 50
    ENABLE_REFERENCE_SHEET = True

    MAX_TASK_STEPS = 50
    MAX_RETRIES = 3
    ACTION_TIMEOUT = 30
    SCREENSHOT_DELAY = 0.5

    MAX_ELEMENTS_TO_ANALYZE = 100
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    MEDIA_DIR = str(PROJECT_ROOT / "media")
    SCREENSHOT_PATH = os.path.join(MEDIA_DIR, "screen.png")
    DEBUG_PATH = os.path.join(MEDIA_DIR, "debug_overlay.png")
    REF_PATH = os.path.join(MEDIA_DIR, "debug_reference.png")
    UAC_TRIGGER_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp", "uac_trigger.txt")
    TEMP_SCREEN_PATH = os.path.join(MEDIA_DIR, "temp_screen.png")

    REQUIRE_CONFIRMATION_FOR = [
        "delete",
        "remove",
        "uninstall",
        "format",
        "shutdown",
        "restart",
        "close",
    ]

    DANGEROUS_COMMANDS = ["rm -rf", "del /f", "format", "diskpart"]

    WAIT_AFTER_CLICK = 0.5
    WAIT_AFTER_TYPE = 0.3
    WAIT_AFTER_KEY = 0.2
    TYPING_INTERVAL = 0.05

    TURBO_MODE = True

    ENABLE_VERIFICATION = True
    VERIFICATION_MIN_CONFIDENCE = 0.7
    VERIFICATION_DELAY = 1.5

    USE_GUI_MODE = True
    CHAT_WINDOW_WIDTH = 800
    CHAT_WINDOW_HEIGHT = 300
    GUI_TRANSPARENCY_LEVEL = 0.8
    GUI_TOGGLE_SHORTCUT = "ctrl+shift+z"

    ENABLE_LOOP_DETECTION = True
    LOOP_DETECTION_THRESHOLD = 3
    LOOP_SCREEN_SIMILARITY_THRESHOLD = 0.95

    APP_INDEX_PATH = os.path.expanduser("~/.pixelpilot/app_index.json")
    APP_INDEX_AUTO_REFRESH = True
    APP_INDEX_INCLUDE_PROCESSES = True

    ENABLE_CLARIFICATION = True
    CLARIFICATION_MIN_CONFIDENCE = 0.7
    CLARIFICATION_TIMEOUT = 60

    ENABLE_BLIND_MODE = True

    SAVE_SCREENSHOTS = True
    VERBOSE_LOGGING = True

    ENABLE_AGENT_DESKTOP = True
    AGENT_DESKTOP_NAME = "PixelPilotAgent"
    SIDECAR_PREVIEW_FPS = 5
    SIDECAR_PREVIEW_WIDTH = 400
    SIDECAR_PREVIEW_HEIGHT = 300
    DEFAULT_WORKSPACE = "user"  # "user" or "agent"

    @classmethod
    def get_mode(cls, mode_str: str = None) -> OperationMode:
        """
        Get operation mode from string or environment variable.

        Args:
            mode_str: Mode string ('guide', 'safe', 'auto')

        Returns:
            OperationMode: The operation mode
        """
        if mode_str is None:
            mode_str = os.getenv("AGENT_MODE", cls.DEFAULT_MODE.value)

        mode_str = mode_str.lower()

        if mode_str == "guide":
            return OperationMode.GUIDE
        elif mode_str == "safe":
            return OperationMode.SAFE
        elif mode_str == "auto":
            return OperationMode.AUTO
        else:
            print(f"Unknown mode '{mode_str}', using default: {cls.DEFAULT_MODE.value}")
            return cls.DEFAULT_MODE

    @classmethod
    def is_dangerous_action(cls, action_description: str) -> bool:
        """
        Check if an action is potentially dangerous.

        Args:
            action_description: Description of the action

        Returns:
            bool: True if action is dangerous
        """
        action_lower = action_description.lower()

        for cmd in cls.DANGEROUS_COMMANDS:
            if cmd.lower() in action_lower:
                return True

        for keyword in cls.REQUIRE_CONFIRMATION_FOR:
            if keyword in action_lower:
                return True

        return False

    @classmethod
    def should_ask_confirmation(cls, mode: OperationMode, action_description: str) -> bool:
        """
        Determine if confirmation should be requested for an action.

        Args:
            mode: Current operation mode
            action_description: Description of the action

        Returns:
            bool: True if confirmation is needed
        """
        if mode == OperationMode.GUIDE:
            return True

        if mode == OperationMode.SAFE:
            return True

        if mode == OperationMode.AUTO:
            return cls.is_dangerous_action(action_description)

        return True

    @classmethod
    def validate(cls):
        """Validate configuration and raise errors if misconfigured."""
        if not cls.GEMINI_API_KEY:
            raise ValueError(
                "Missing GEMINI_API_KEY! Set it in your .env file or environment variables."
            )

        print("Configuration validated successfully")
        print(f"   Model: {cls.GEMINI_MODEL}")
        print(f"   Default Mode: {cls.DEFAULT_MODE.value}")
        print(f"   Turbo Mode: {'ENABLED' if cls.TURBO_MODE else 'DISABLED'}")
