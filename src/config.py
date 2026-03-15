import os
import logging
from pathlib import Path
from enum import Enum
from dotenv import load_dotenv

load_dotenv()


class OperationMode(Enum):
    GUIDE = "guide"
    SAFE = "safe"
    AUTO = "auto"

logger = logging.getLogger("pixelpilot.config")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    BACKEND_URL = os.getenv("BACKEND_URL", "https://pixel-pilot-5jpy.onrender.com")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    GEMINI_BASE_MODEL = os.getenv("GEMINI_BASE_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    USE_DIRECT_API = bool(GEMINI_API_KEY)
    ENABLE_GEMINI_LIVE_MODE = _env_bool("ENABLE_GEMINI_LIVE_MODE", True)
    GEMINI_LIVE_MODEL = os.getenv(
        "GEMINI_LIVE_MODEL",
        "gemini-2.5-flash-native-audio-preview-12-2025",
    ).strip() or "gemini-2.5-flash-native-audio-preview-12-2025"
    LIVE_VIDEO_FPS = max(1, int(os.getenv("LIVE_VIDEO_FPS", "1") or "1"))
    LIVE_AUDIO_INPUT_RATE = max(8000, int(os.getenv("LIVE_AUDIO_INPUT_RATE", "16000") or "16000"))
    LIVE_AUDIO_OUTPUT_RATE = max(8000, int(os.getenv("LIVE_AUDIO_OUTPUT_RATE", "24000") or "24000"))
    LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE = max(
        30,
        int(os.getenv("LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE", "105") or "105"),
    )
    LIVE_MODE_AVAILABLE = bool(ENABLE_GEMINI_LIVE_MODE and USE_DIRECT_API)

    DEFAULT_MODE = OperationMode(os.getenv("DEFAULT_MODE", OperationMode.AUTO.value))
    VISION_MODE = os.getenv("VISION_MODE", "ocr").strip().lower()

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
    APP_LAUNCH_WAIT = 3
    ENABLE_UIA_BLIND_MODE = True
    UIA_MAX_ELEMENTS = 120
    UIA_MAX_WINDOWS = 40
    UIA_TEXT_MAX_CHARS = 4000
    UIA_TEXT_OCR_MIN_CHARS = 160
    UIA_TEXT_OCR_MAX_NOISE_RATIO = 0.18
    UIA_TEXT_USE_OCR_FALLBACK_DEFAULT = False

    try:
        BLIND_VERIFICATION_RETRIES = max(0, int(os.getenv("BLIND_VERIFICATION_RETRIES", "2")))
    except Exception:
        BLIND_VERIFICATION_RETRIES = 2

    try:
        BLIND_VERIFICATION_RETRY_DELAY = max(
            0.0, float(os.getenv("BLIND_VERIFICATION_RETRY_DELAY", "0.25"))
        )
    except Exception:
        BLIND_VERIFICATION_RETRY_DELAY = 0.25

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
            logger.warning(f"Unknown mode '{mode_str}', using default: {cls.DEFAULT_MODE.value}")
            return cls.DEFAULT_MODE

    @classmethod
    def is_dangerous_action(cls, action_description: str) -> bool:
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
        if mode == OperationMode.GUIDE:
            return False

        if mode == OperationMode.SAFE:
            return cls.is_dangerous_action(action_description)

        if mode == OperationMode.AUTO:
            return False

        return True

    @classmethod
    def validate(cls):
        logger.info("Configuration validated successfully")
        logger.debug(f"Model: {cls.GEMINI_MODEL}")
        logger.debug(f"Backend: {cls.BACKEND_URL}")
        logger.debug(f"Default Mode: {cls.DEFAULT_MODE.value}")
        logger.debug(f"Turbo Mode: {'ENABLED' if cls.TURBO_MODE else 'DISABLED'}")

    @classmethod
    def clear_api_key(cls):
        """Clears the GEMINI_API_KEY from environment and .env file."""
        cls.GEMINI_API_KEY = None
        cls.USE_DIRECT_API = False
        cls.LIVE_MODE_AVAILABLE = bool(cls.ENABLE_GEMINI_LIVE_MODE and cls.USE_DIRECT_API)
        os.environ.pop("GEMINI_API_KEY", None)

        env_path = cls.PROJECT_ROOT / ".env"
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                new_lines = [l for l in lines if not l.strip().startswith("GEMINI_API_KEY=")]
                
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                logger.info("Removed GEMINI_API_KEY from .env")
            except Exception as e:
                logger.error(f"Failed to clear API key from .env: {e}")
