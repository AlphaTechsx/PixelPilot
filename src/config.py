import os
import logging
from pathlib import Path
from enum import Enum
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class OperationMode(Enum):
    GUIDE = "guide"
    SAFE = "safe"
    AUTO = "auto"

logger = logging.getLogger("pixelpilot.config")


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return str(default).strip()
    value = raw.strip()
    return value or str(default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    value = raw.strip().lower()
    if not value:
        return bool(default)
    if value == "true":
        return True
    if value == "false":
        return False
    return bool(default)


def _env_int(name: str, default: int, *, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


class Config:
    BACKEND_URL = _env_str(
        "BACKEND_URL",
        "https://pixelpilot-backend-564947821962.us-central1.run.app",
    )
    GEMINI_MODEL = _env_str("GEMINI_MODEL", "gemini-3-flash-preview")
    GEMINI_API_KEY = _env_str("GEMINI_API_KEY")
    USE_DIRECT_API = bool(GEMINI_API_KEY)
    ENABLE_GEMINI_LIVE_MODE = _env_bool("ENABLE_GEMINI_LIVE_MODE", True)
    LIVE_MODE_DEFAULT_ENABLED = _env_bool("LIVE_MODE_DEFAULT_ENABLED", True)
    LIVE_MODE_DEFAULT_VOICE_ENABLED = _env_bool("LIVE_MODE_DEFAULT_VOICE_ENABLED", True)
    GEMINI_LIVE_MODEL = _env_str(
        "GEMINI_LIVE_MODEL",
        "gemini-2.5-flash-native-audio-preview-12-2025",
    )
    _LIVE_MODEL_LOWER = GEMINI_LIVE_MODEL.lower()
    LIVE_ENABLE_IMAGE_INPUT = _env_bool(
        "LIVE_ENABLE_IMAGE_INPUT",
        "native-audio" not in _LIVE_MODEL_LOWER,
    )
    LIVE_ENABLE_VIDEO_STREAM = _env_bool("LIVE_ENABLE_VIDEO_STREAM", LIVE_ENABLE_IMAGE_INPUT)
    LIVE_VIDEO_FPS = _env_int("LIVE_VIDEO_FPS", 1, minimum=1)
    LIVE_AUDIO_INPUT_RATE = _env_int("LIVE_AUDIO_INPUT_RATE", 16000, minimum=8000)
    LIVE_AUDIO_OUTPUT_RATE = _env_int("LIVE_AUDIO_OUTPUT_RATE", 24000, minimum=8000)
    LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS = _env_int(
        "LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS",
        192,
        minimum=16,
    )
    LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS = _env_int(
        "LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS",
        LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS,
        minimum=16,
    )
    LIVE_AUDIO_SPEAKER_QUEUE_TRIM_TO_CHUNKS = max(
        4,
        min(
            LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS - 1,
            _env_int("LIVE_AUDIO_SPEAKER_QUEUE_TRIM_TO_CHUNKS", 144),
        ),
    )
    LIVE_AUDIO_SPEAKER_BATCH_MAX_CHUNKS = _env_int(
        "LIVE_AUDIO_SPEAKER_BATCH_MAX_CHUNKS",
        8,
        minimum=1,
    )
    LIVE_AUDIO_SPEAKER_BATCH_MAX_BYTES = _env_int(
        "LIVE_AUDIO_SPEAKER_BATCH_MAX_BYTES",
        65536,
        minimum=4096,
    )
    LIVE_AUDIO_LOSSLESS_MODE = _env_bool("LIVE_AUDIO_LOSSLESS_MODE", True)
    LIVE_AUDIO_MIC_SUPPRESS_TAIL_MS = _env_int("LIVE_AUDIO_MIC_SUPPRESS_TAIL_MS", 220, minimum=0)
    LIVE_TEXT_SEND_TIMEOUT_SECONDS = _env_float(
        "LIVE_TEXT_SEND_TIMEOUT_SECONDS",
        8.0,
        minimum=1.0,
    )
    LIVE_CONNECT_RETRY_BASE_DELAY_SECONDS = _env_float(
        "LIVE_CONNECT_RETRY_BASE_DELAY_SECONDS",
        0.75,
        minimum=0.1,
    )
    LIVE_GUIDANCE_OBSERVER_POLL_SECONDS = _env_float(
        "LIVE_GUIDANCE_OBSERVER_POLL_SECONDS",
        1.0,
        minimum=0.1,
    )
    LIVE_GUIDANCE_OBSERVER_NUDGE_COOLDOWN_SECONDS = _env_float(
        "LIVE_GUIDANCE_OBSERVER_NUDGE_COOLDOWN_SECONDS",
        2.5,
        minimum=0.1,
    )
    LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_CHUNKS = _env_int(
        "LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_CHUNKS",
        max(1, min(LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS, 144)),
        minimum=1,
        maximum=LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS,
    )
    LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_COOLDOWN_SECONDS = _env_float(
        "LIVE_AUDIO_LOSSLESS_BACKLOG_WARNING_COOLDOWN_SECONDS",
        3.0,
        minimum=0.1,
    )
    LIVE_AUDIO_QUEUE_PUT_TIMEOUT_SECONDS = _env_float(
        "LIVE_AUDIO_QUEUE_PUT_TIMEOUT_SECONDS",
        0.20,
        minimum=0.01,
    )
    LIVE_AUDIO_QUEUE_DROP_LOG_COOLDOWN_SECONDS = _env_float(
        "LIVE_AUDIO_QUEUE_DROP_LOG_COOLDOWN_SECONDS",
        2.0,
        minimum=0.1,
    )
    LIVE_AUDIO_RESAMPLE_LOG_COOLDOWN_SECONDS = _env_float(
        "LIVE_AUDIO_RESAMPLE_LOG_COOLDOWN_SECONDS",
        5.0,
        minimum=0.1,
    )
    LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE = _env_int(
        "LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE",
        105,
        minimum=30,
    )
    LIVE_MODE_AVAILABLE = bool(ENABLE_GEMINI_LIVE_MODE)
    ENABLE_GATEWAY = _env_bool("ENABLE_GATEWAY", False)
    GATEWAY_HOST = _env_str("GATEWAY_HOST", "localhost")
    GATEWAY_PORT = _env_int("GATEWAY_PORT", 8765, minimum=1)
    GATEWAY_COMMAND_TIMEOUT_SECONDS = _env_float(
        "GATEWAY_COMMAND_TIMEOUT_SECONDS",
        120.0,
        minimum=5.0,
    )
    GATEWAY_TOKEN = _env_str("PIXELPILOT_GATEWAY_TOKEN")

    DEFAULT_MODE = OperationMode(_env_str("DEFAULT_MODE", OperationMode.AUTO.value).lower())
    VISION_MODE = _env_str("VISION_MODE", "ocr").lower()

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
    TEMP_SCREEN_PATH = os.path.join(MEDIA_DIR, "temp_screen.png")
    UAC_IPC_DIR = _env_str(
        "UAC_IPC_DIR",
        os.path.join(
            os.environ.get("ProgramData", r"C:\ProgramData"),
            "PixelPilot",
            "uac",
        ),
    )
    UAC_REQUEST_MAX_AGE_SECONDS = _env_float("UAC_REQUEST_MAX_AGE_SECONDS", 90.0, minimum=30.0)
    UAC_RESPONSE_TIMEOUT_SECONDS = _env_float("UAC_RESPONSE_TIMEOUT_SECONDS", 15.0, minimum=2.0)
    UAC_IPC_POLL_INTERVAL_SECONDS = _env_float(
        "UAC_IPC_POLL_INTERVAL_SECONDS",
        0.5,
        minimum=0.1,
    )
    UAC_HELPER_INITIAL_CAPTURE_DELAY_SECONDS = _env_float(
        "UAC_HELPER_INITIAL_CAPTURE_DELAY_SECONDS",
        1.0,
        minimum=0.0,
    )
    UAC_HELPER_KEY_PRESS_DELAY_SECONDS = _env_float(
        "UAC_HELPER_KEY_PRESS_DELAY_SECONDS",
        0.05,
        minimum=0.01,
    )
    UAC_HELPER_POST_ACTION_DELAY_SECONDS = _env_float(
        "UAC_HELPER_POST_ACTION_DELAY_SECONDS",
        0.1,
        minimum=0.0,
    )
    UAC_CAPTURE_SETTLE_AFTER_RESPONSE_SECONDS = _env_float(
        "UAC_CAPTURE_SETTLE_AFTER_RESPONSE_SECONDS",
        2.0,
        minimum=0.0,
    )

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
    ENABLE_UIA_FOR_AGENT_WORKSPACE = _env_bool("ENABLE_UIA_FOR_AGENT_WORKSPACE", True)
    UIA_MAX_ELEMENTS = 120
    UIA_MAX_WINDOWS = 40
    UIA_TEXT_MAX_CHARS = 4000
    UIA_TEXT_OCR_MIN_CHARS = 160
    UIA_TEXT_OCR_MAX_NOISE_RATIO = 0.18
    UIA_TEXT_USE_OCR_FALLBACK_DEFAULT = False

    BLIND_VERIFICATION_RETRIES = _env_int("BLIND_VERIFICATION_RETRIES", 2, minimum=0)
    BLIND_VERIFICATION_RETRY_DELAY = _env_float(
        "BLIND_VERIFICATION_RETRY_DELAY",
        0.25,
        minimum=0.0,
    )

    SAVE_SCREENSHOTS = True
    VERBOSE_LOGGING = True

    ENABLE_AGENT_DESKTOP = True
    AGENT_DESKTOP_NAME = "PixelPilotAgent"
    SIDECAR_PREVIEW_FPS = 5
    SIDECAR_PREVIEW_WIDTH = 400
    SIDECAR_PREVIEW_HEIGHT = 300
    DEFAULT_WORKSPACE = "user"  # "user" or "agent"

    @classmethod
    def get_mode(cls, mode_str: Optional[str] = None) -> OperationMode:
        """
        Get operation mode from string or environment variable.

        Args:
            mode_str: Mode string ('guide', 'safe', 'auto')

        Returns:
            OperationMode: The operation mode
        """
        if mode_str is None:
            mode_str = cls.DEFAULT_MODE.value
            
        if not isinstance(mode_str, str):
            mode_str = str(mode_str)

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
        cls.LIVE_MODE_AVAILABLE = bool(cls.ENABLE_GEMINI_LIVE_MODE)
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
