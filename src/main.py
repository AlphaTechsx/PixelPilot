import sys
import os
import ctypes
import logging
from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtCore import qInstallMessageHandler, Qt
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QPainter, QColor
from PySide6.QtWidgets import QMessageBox
from PySide6.QtSvg import QSvgRenderer

class GuiStream:
    def __init__(self, logger: logging.Logger, is_error: bool = False):
        self.logger = logger
        self.is_error = is_error
        self.buffer = ""

    def _classify_level(self, line: str) -> int:
        s = (line or "").strip()
        if not s:
            return logging.INFO

        # Default policy: stdout is treated as debug noise unless it clearly indicates a problem.
        if not self.is_error:
            if "error" in s.lower() or "failed" in s.lower() or s.lower().startswith("exception"):
                return logging.ERROR
            if "warning" in s.lower() or s.lower().startswith("warning:"):
                return logging.WARNING
            return logging.DEBUG

        # Many backend debug prints use indentation / arrows.
        if s.startswith("->") or s.startswith("   ->") or s.startswith("[") or s.startswith("   ["):
            if "error" in s.lower() or "failed" in s.lower():
                return logging.ERROR
            if "warning" in s.lower() or "warn" in s.lower():
                return logging.WARNING
            return logging.DEBUG

        if s.lower().startswith("warning:"):
            return logging.WARNING

        return logging.ERROR

    def write(self, data):
        if not data: return
        self.buffer += str(data)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip()
            if not line: continue

            # Avoid spamming GUI with known noise; still goes to file via Qt handler.
            noise = (
                "Using CPU.",
                "qt.qpa.window:",
                "SetProcessDpiAwarenessContext() failed",
                "Qt's default DPI awareness context",
                "QFont::setPointSize: Point size <= 0",
            )
            if any(p in line for p in noise):
                self.logger.debug(line)
                continue

            level = self._classify_level(line)
            self.logger.log(level, line)

    def flush(self):
        pass


def _install_qt_message_collector(early_messages: list[tuple[object, str]]):
    def _handler(mode, context, message):
        if message:
            early_messages.append((mode, str(message)))

    qInstallMessageHandler(_handler)


def _install_qt_message_router(logger: logging.Logger):
    def _handler(mode, context, message):
        if not message:
            return

        text = str(message).strip()
        if not text:
            return


        if "qt.qpa.window:" in text and "SetProcessDpiAwarenessContext() failed" in text:
            logger.debug(text)
            return
        if "SetProcessDpiAwarenessContext() failed: Access is denied." in text:
            logger.debug(text)
            return
        if "Qt's default DPI awareness context" in text:
            logger.debug(text)
            return
        if "QFont::setPointSize: Point size <= 0" in text:
            logger.debug(text)
            return

        mode_name = getattr(mode, "name", "").lower()
        if "warning" in mode_name or "critical" in mode_name or "fatal" in mode_name:
            logger.warning(text)
        else:
            logger.debug(text)

    qInstallMessageHandler(_handler)

def main():
    app = QApplication(sys.argv)
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos", "pixelpilot-logo-creative.ico")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos", "pixelpilot-logo-creative.svg")

    splash = None
    if os.path.exists(logo_path):
        if logo_path.endswith(".svg"):
            renderer = QSvgRenderer(logo_path)
            if renderer.isValid():
                pixmap = QPixmap(800, 300)
                pixmap.fill(QColor("transparent"))
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)
        else:
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                 splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)

        if splash:
            splash.show()
            app.processEvents()

    from ui.main_window import MainWindow
    from ui.gui_adapter import GuiAdapter
    from core.controller import MainController
    from core.logging_setup import configure_logging, attach_gui_logging
    from ui.global_hotkeys import GlobalHotkeyManager, MOD_CONTROL, MOD_SHIFT

    early_qt_messages: list[tuple[object, str]] = []
    _install_qt_message_collector(early_qt_messages)

    logger, buffered_gui, log_file_path = configure_logging(adapter=None)
    logger.debug("Pixel Pilot starting")

    def is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def relaunch_as_admin() -> bool:
        try:
            python_exe = sys.executable
            candidate_pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
            if os.path.exists(candidate_pythonw):
                python_exe = candidate_pythonw

            script_path = os.path.abspath(__file__)
            params = f'"{script_path}"'

            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", python_exe, params, None, 1)
            return int(rc) > 32
        except Exception:
            return False

    if not is_admin():
        if relaunch_as_admin():
            return

        QMessageBox.warning(
            None,
            "Administrator Privileges",
            "Pixel Pilot is running without Administrator privileges.\n\n"
            "Some desktop automation features may be limited.",
        )

    window = MainWindow()
    
    adapter = GuiAdapter()
    controller = MainController(adapter, window)
    
    # Wire Adapter -> ChatWidget
    adapter.system_message_received.connect(window.chat_widget.add_system_message)
    adapter.output_message_received.connect(window.chat_widget.add_output_message)
    adapter.error_message_received.connect(window.chat_widget.add_error_message)
    adapter.user_message_received.connect(window.chat_widget.add_user_message)
    adapter.activity_message_received.connect(window.chat_widget.add_activity_message)
    adapter.final_answer_received.connect(window.chat_widget.add_final_answer)

    # Attach GUI logging now that adapter exists.
    attach_gui_logging(logger, adapter, buffered_gui)

    # Now that adapter exists, route any future Qt messages into the logger.
    _install_qt_message_router(logger)

    # Redirect stdout/stderr so backend prints become structured GUI logs
    sys.stdout = GuiStream(logger, is_error=False)
    sys.stderr = GuiStream(logger, is_error=True)

    # Wire ChatWidget -> Controller
    window.chat_widget.command_received.connect(controller.handle_user_command)
    window.chat_widget.mode_changed.connect(controller.handle_mode_changed)
    window.chat_widget.vision_changed.connect(controller.handle_vision_changed)

    # Global shortcuts
    # Keep references; otherwise Python GC can deactivate QShortcut.
    window._qt_shortcuts = []

    toggle_interactive = QShortcut(QKeySequence("Ctrl+Shift+Z"), window)
    toggle_interactive.setContext(Qt.ShortcutContext.ApplicationShortcut)
    toggle_interactive.activated.connect(controller.toggle_click_through)
    window._qt_shortcuts.append(toggle_interactive)

    stop_request = QShortcut(QKeySequence("Ctrl+Shift+X"), window)
    stop_request.setContext(Qt.ShortcutContext.ApplicationShortcut)
    stop_request.activated.connect(controller.stop_current_request)
    window._qt_shortcuts.append(stop_request)

    close_app = QShortcut(QKeySequence("Ctrl+Shift+Q"), window)
    close_app.setContext(Qt.ShortcutContext.ApplicationShortcut)
    close_app.activated.connect(QApplication.quit)
    window._qt_shortcuts.append(close_app)

    # Windows system-wide hotkeys (work even when the overlay is click-through/unfocused)
    hotkeys = GlobalHotkeyManager(parent=window)
    window._global_hotkeys = hotkeys

    HK_TOGGLE = 1
    HK_STOP = 2
    HK_CLOSE = 3

    hotkeys.register(HK_TOGGLE, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("Z"))
    hotkeys.register(HK_STOP, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("X"))
    hotkeys.register(HK_CLOSE, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("Q"))

    def _on_hotkey(hotkey_id: int):
        if hotkey_id == HK_TOGGLE:
            controller.toggle_click_through()
        elif hotkey_id == HK_STOP:
            controller.stop_current_request()
        elif hotkey_id == HK_CLOSE:
            QApplication.quit()

    hotkeys.activated.connect(_on_hotkey)
    
    # Initialize Agent (keep startup info out of main chat)
    adapter.add_activity_message("Startup")
    adapter.add_activity_message(f"Logging to: {log_file_path}")
    adapter.add_activity_message(f"Admin: {'YES' if is_admin() else 'NO'}")
    controller.init_agent()
    if controller.agent:
        window.chat_widget.set_operation_mode(controller.agent.mode)
        window.chat_widget.set_vision_mode("ROBO" if controller.agent.robotics_eye else "OCR")

    # Flush early Qt messages with best-effort severity classification
    for mode, msg in early_qt_messages:
        mode_name = getattr(mode, "name", "").lower()
        if "QFont::setPointSize: Point size <= 0" in msg:
            logger.debug(msg)
            continue
        if "qt.qpa.window:" in msg and "SetProcessDpiAwarenessContext() failed" in msg:
            logger.debug(msg)
            continue
        if "SetProcessDpiAwarenessContext() failed: Access is denied." in msg:
            logger.debug(msg)
            continue
        if "Qt's default DPI awareness context" in msg:
            logger.debug(msg)
            continue
        if "warning" in mode_name or "critical" in mode_name or "fatal" in mode_name:
            logger.warning(msg)
        else:
            logger.debug(msg)
    
    window.show()
    # Close splash screen when main window is visible
    if splash:
        splash.finish(window)
        
    logger.debug("Pixel Pilot GUI shown")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
