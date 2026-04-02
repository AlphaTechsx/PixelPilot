import sys
import os
import ctypes
import logging
import time
from PySide6.QtWidgets import QApplication, QSplashScreen, QSystemTrayIcon, QMenu
from PySide6.QtCore import qInstallMessageHandler, Qt, QTimer
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QPainter, QColor, QAction, QIcon
from dotenv import load_dotenv
from config import Config

load_dotenv()

class GuiStream:
    def __init__(self, logger: logging.Logger, is_error: bool = False):
        self.logger = logger
        self.is_error = is_error
        self.buffer = ""

    def _classify_level(self, line: str) -> int:
        s = (line or "").strip()
        if not s:
            return logging.INFO

        if not self.is_error:
            if "error" in s.lower() or "failed" in s.lower() or s.lower().startswith("exception"):
                return logging.ERROR
            if "warning" in s.lower() or s.lower().startswith("warning:"):
                return logging.WARNING
            return logging.DEBUG

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
    process_started_at = time.perf_counter()
    app = QApplication(sys.argv)

    if not Config.USE_DIRECT_API:
        from ui.login_dialog import require_login

        if not require_login():
            print("Login cancelled. Exiting.")
            sys.exit(0)

    from core.logging_setup import configure_logging, attach_gui_logging

    early_qt_messages: list[tuple[object, str]] = []
    _install_qt_message_collector(early_qt_messages)

    logger, buffered_gui, log_file_path = configure_logging(adapter=None)
    startup_logger = logging.getLogger("pixelpilot.startup")
    startup_logger.info(
        "STARTUP phase=process_start status=ok elapsed_ms=%d",
        int((time.perf_counter() - process_started_at) * 1000),
    )
    logger.debug("Pixel Pilot starting")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    splash_logo_path = os.path.join(base_dir, "logos", "pixelpilot-logo-creative.ico")
    if not os.path.exists(splash_logo_path):
        splash_logo_path = os.path.join(base_dir, "logos", "pixelpilot-logo-creative.svg")

    task_icon_path = os.path.join(base_dir, "logos", "pixelpilot-icon.ico")
    if not os.path.exists(task_icon_path):
        task_icon_path = os.path.join(base_dir, "logos", "pixelpilot-icon.svg")
    if not os.path.exists(task_icon_path):
        task_icon_path = splash_logo_path

    splash = None
    if os.path.exists(splash_logo_path):
        if splash_logo_path.endswith(".svg"):
            try:
                from PySide6.QtSvg import QSvgRenderer

                renderer = QSvgRenderer(splash_logo_path)
                if renderer.isValid():
                    pixmap = QPixmap(800, 300)
                    pixmap.fill(QColor("transparent"))
                    painter = QPainter(pixmap)
                    renderer.render(painter)
                    painter.end()
                    splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)
            except Exception:
                splash = None
        else:
            pixmap = QPixmap(splash_logo_path)
            if not pixmap.isNull():
                 splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint)

        if splash:
            splash.show()
            app.processEvents()

    from ui.main_window import MainWindow
    from ui.gui_adapter import GuiAdapter
    from core.controller import MainController

    def is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    try:
        window = MainWindow()
    except Exception:
        logger.exception("Failed to construct main window")
        if splash:
            splash.close()
        raise
    if os.path.exists(task_icon_path):
        window.setWindowIcon(QIcon(task_icon_path))
    
    adapter = GuiAdapter()
    controller = MainController(
        adapter,
        window,
        startup_started_at=process_started_at,
    )
    
    # Wire Adapter -> ChatWidget
    adapter.system_message_received.connect(window.chat_widget.add_system_message)
    adapter.output_message_received.connect(window.chat_widget.add_output_message)
    adapter.error_message_received.connect(window.chat_widget.add_error_message)
    adapter.user_message_received.connect(window.chat_widget.add_user_message)
    adapter.activity_message_received.connect(window.chat_widget.add_activity_message)
    adapter.final_answer_received.connect(window.chat_widget.add_final_answer)
    adapter.live_transcript_received.connect(window.chat_widget.on_live_transcript)
    adapter.live_session_state_received.connect(window.chat_widget.set_live_session_state)
    adapter.live_action_state_received.connect(window.chat_widget.on_live_action_state)
    adapter.live_audio_level_received.connect(window.chat_widget.on_live_audio_level)
    adapter.assistant_audio_level_received.connect(window.chat_widget.on_assistant_audio_level)
    adapter.live_availability_received.connect(window.chat_widget.set_live_availability)
    adapter.live_voice_active_received.connect(window.chat_widget.set_live_voice_active)

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
    window.chat_widget.live_mode_changed.connect(controller.handle_live_mode_changed)
    window.chat_widget.live_voice_toggled.connect(controller.handle_live_voice_toggled)
    window.chat_widget.agent_view_visibility_changed.connect(controller.update_sidecar_visibility)

    # Logout handler
    def handle_logout():
        from auth_manager import get_auth_manager
        from ui.login_dialog import LoginDialog

        Config.clear_api_key()
        get_auth_manager().logout()
        window.hide()

        dialog = LoginDialog()
        if dialog.exec() and dialog.success:
            window.show()
        else:
            QApplication.quit()

    window.chat_widget.logout_btn.clicked.connect(handle_logout)

    window.show()
    app.processEvents()
    if splash:
        splash.finish(window)
    app.processEvents()

    logging.getLogger("pixelpilot.agent").debug("Pixel Pilot GUI shown")
    controller.mark_startup_phase("first_paint")

    from ui.global_hotkeys import GlobalHotkeyManager, MOD_CONTROL, MOD_SHIFT

    # System tray (background minimize/restore)
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(window)
        tray.setToolTip("Pixel Pilot")
        if os.path.exists(task_icon_path):
            tray.setIcon(QIcon(task_icon_path))
        else:
            tray.setIcon(window.windowIcon())

        tray_menu = QMenu()
        show_action = QAction("Show Pixel Pilot", tray_menu)
        hide_action = QAction("Hide to Background", tray_menu)
        quit_action = QAction("Quit", tray_menu)

        show_action.triggered.connect(window.restore_from_background)
        hide_action.triggered.connect(window.minimize_to_background)
        quit_action.triggered.connect(QApplication.quit)

        tray_menu.addAction(show_action)
        tray_menu.addAction(hide_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        tray.setContextMenu(tray_menu)

        def _on_tray_activated(reason):
            if reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            ):
                window.restore_from_background()

        tray.activated.connect(_on_tray_activated)
        tray.show()

    window._system_tray = tray

    # Global shortcuts
    # Keep references; otherwise Python GC can deactivate QShortcut.
    window._qt_shortcuts = []

    toggle_interactive = QShortcut(QKeySequence("Ctrl+Shift+Z"), window)
    toggle_interactive.setContext(Qt.ShortcutContext.ApplicationShortcut)
    toggle_interactive.activated.connect(controller.toggle_click_through)
    window._qt_shortcuts.append(toggle_interactive)

    stop_request = QShortcut(QKeySequence("Ctrl+Shift+X"), window)
    stop_request.setContext(Qt.ShortcutContext.ApplicationShortcut)
    stop_request.activated.connect(controller.stop_current_turn)
    window._qt_shortcuts.append(stop_request)

    close_app = QShortcut(QKeySequence("Ctrl+Shift+Q"), window)
    close_app.setContext(Qt.ShortcutContext.ApplicationShortcut)
    close_app.activated.connect(QApplication.quit)
    window._qt_shortcuts.append(close_app)

    background_toggle = QShortcut(QKeySequence("Ctrl+Shift+M"), window)
    background_toggle.setContext(Qt.ShortcutContext.ApplicationShortcut)
    background_toggle.activated.connect(window.toggle_background_visibility)
    window._qt_shortcuts.append(background_toggle)

    details_toggle = QShortcut(QKeySequence("Ctrl+Shift+D"), window)
    details_toggle.setContext(Qt.ShortcutContext.ApplicationShortcut)
    details_toggle.activated.connect(window.toggle_expand)
    window._qt_shortcuts.append(details_toggle)

    # Windows system-wide hotkeys (work even when the overlay is click-through/unfocused)
    hotkeys = GlobalHotkeyManager(parent=window)
    window._global_hotkeys = hotkeys

    HK_TOGGLE = 1
    HK_STOP = 2
    HK_CLOSE = 3
    HK_BACKGROUND = 4
    HK_DETAILS = 5

    hotkeys.register(HK_TOGGLE, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("Z"))
    hotkeys.register(HK_STOP, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("X"))
    hotkeys.register(HK_CLOSE, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("Q"))
    hotkeys.register(HK_BACKGROUND, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("M"))
    hotkeys.register(HK_DETAILS, modifiers=MOD_CONTROL | MOD_SHIFT, vk=ord("D"))

    def _on_hotkey(hotkey_id: int):
        if hotkey_id == HK_TOGGLE:
            controller.toggle_click_through()
        elif hotkey_id == HK_STOP:
            controller.stop_current_turn()
        elif hotkey_id == HK_CLOSE:
            QApplication.quit()
        elif hotkey_id == HK_BACKGROUND:
            window.toggle_background_visibility()
        elif hotkey_id == HK_DETAILS:
            window.toggle_expand()

    hotkeys.activated.connect(_on_hotkey)
    
    adapter.add_activity_message("Startup")
    adapter.add_activity_message(f"Logging to: {log_file_path}")
    adapter.add_activity_message(f"Admin: {'YES' if is_admin() else 'NO'}")
    QTimer.singleShot(0, controller.start_bootstrap)

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

    app.aboutToQuit.connect(controller.shutdown)
    sys.exit(app.exec())
if __name__ == "__main__":
    main()
