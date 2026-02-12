import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


class GuiFormatter(logging.Formatter):
    """Formatter for GUI logs: never append exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        exc_info = record.exc_info
        exc_text = getattr(record, "exc_text", None)
        record.exc_info = None
        record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_info = exc_info
            record.exc_text = exc_text


class BufferingGuiHandler(logging.Handler):
    """Buffers formatted GUI log lines until a GuiAdapter is available."""

    def __init__(self, level: int = logging.INFO):
        super().__init__(level=level)
        self.lines: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if msg:
                self.lines.append((record.levelno, msg))
        except Exception:
            pass


class GuiLogHandler(logging.Handler):
    def __init__(self, adapter, level: int = logging.INFO):
        super().__init__(level=level)
        self.adapter = adapter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = record.getMessage() if record else ""
            if not raw:
                return

            text = str(raw).strip()
            if not text:
                return

            low = text.lower()

            internal_activity_markers = (
                "taking screenshot",
                "screenshot attempt",
                "uac detected",
                "orchestrator protocol",
                "standard screenshot failed",
                "verification screenshot failed",
                "trusting task completion",
                "error parsing gemini response",
            )
            if any(m in low for m in internal_activity_markers):
                self.adapter.add_activity_message(text)
                return

            if "[reply]" in low:
                reply = text
                idx = reply.lower().find("[reply]")
                if idx >= 0:
                    reply = reply[idx + len("[reply]"):].lstrip(" :\t")
                if reply:
                    add_final = getattr(self.adapter, "add_final_answer", None)
                    if callable(add_final):
                        add_final(reply)
                    else:
                        self.adapter.add_system_message(reply)
                return

            if record.levelno == logging.DEBUG:
                activity_markers = (
                    "executing:",
                    "new task",
                    "--- step",
                    "planning action",
                    "action:",
                    "reasoning:",
                    "task completed",
                    "task completed:",
                    "task completed!",
                    "task completed",
                    "step ",
                )
                if low.startswith(activity_markers) or "planning action" in low or "step" in low:
                    self.adapter.add_activity_message(text)
                return

            if record.levelno >= logging.ERROR:
                self.adapter.add_error_message(text)
            elif record.levelno >= logging.WARNING:
                self.adapter.add_activity_message(f"WARN: {text}")
            else:
                self.adapter.add_output_message(text)
        except Exception:
            pass


class GuiNoiseFilter(logging.Filter):
    """Drop known noisy, low-value lines from GUI output."""

    _DROP_SUBSTRINGS = (
        "Using CPU.",
        "qt.qpa.window:",
        "SetProcessDpiAwarenessContext() failed",
        "Qt's default DPI awareness context",
        "QFont::setPointSize: Point size <= 0",
    )

    _HIDE_INTERNAL_SUBSTRINGS = (
        "blind mode",
        "[blind]",
        "[mode switch]",
        "activating vision system",
        "switching to vision",
        "vision requested",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage() if record else ""
        if not msg:
            return True

        low = str(msg).lower()

        if record.levelno >= logging.ERROR:
            return True

        if any(s in low for s in self._HIDE_INTERNAL_SUBSTRINGS):
            return False

        if getattr(record, "name", "").startswith("pixelpilot.agent") and record.levelno < logging.WARNING:
            return False

        return not any(s in msg for s in self._DROP_SUBSTRINGS)


def _repo_root_from_src() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def configure_logging(*, adapter=None) -> tuple[logging.Logger, Optional[BufferingGuiHandler], str]:
    """Configure Pixel Pilot logging.

    Returns (logger, buffering_handler_if_any, log_file_path).
    """

    repo_root = _repo_root_from_src()
    log_dir = os.path.join(repo_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file_path = os.path.join(log_dir, "pixelpilot.log")

    logger = logging.getLogger("pixelpilot")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for h in list(logger.handlers):
        logger.removeHandler(h)

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    buffering: Optional[BufferingGuiHandler] = None

    if adapter is None:
        buffering = BufferingGuiHandler(level=logging.INFO)
        buffering.setFormatter(GuiFormatter(fmt="%(asctime)s • %(levelname)s • %(message)s", datefmt="%H:%M:%S"))
        buffering.addFilter(GuiNoiseFilter())
        logger.addHandler(buffering)
    else:
        gui_handler = GuiLogHandler(adapter, level=logging.DEBUG)
        gui_handler.setFormatter(GuiFormatter(fmt="%(asctime)s • %(levelname)s • %(message)s", datefmt="%H:%M:%S"))
        gui_handler.addFilter(GuiNoiseFilter())
        logger.addHandler(gui_handler)

    return logger, buffering, log_file_path


def attach_gui_logging(logger: logging.Logger, adapter, buffering: Optional[BufferingGuiHandler]) -> None:
    """Attach GUI handler and flush buffered GUI lines."""

    if buffering is not None:
        try:
            logger.removeHandler(buffering)
        except Exception:
            pass

    gui_handler = GuiLogHandler(adapter, level=logging.DEBUG)
    gui_handler.setFormatter(GuiFormatter(fmt="%(asctime)s • %(levelname)s • %(message)s", datefmt="%H:%M:%S"))
    gui_handler.addFilter(GuiNoiseFilter())
    logger.addHandler(gui_handler)

    if buffering is not None:
        for levelno, line in buffering.lines:
            if levelno >= logging.ERROR:
                adapter.add_error_message(line)
            elif levelno >= logging.WARNING:
                adapter.add_output_message(f"WARN: {line}")
            else:
                adapter.add_output_message(line)
        buffering.lines.clear()
