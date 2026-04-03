from typing import Optional

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow

from config import Config
from .chat_widget import ChatWidget
from .glass import BackdropController
from .minimized_notch import MinimizedNotchWindow
from .sidecar_preview import SidecarPreview


class MainWindow(QMainWindow):
    BAR_SIZE = (920, 84)
    EXTENDED_SIZE = (920, 660)

    def __init__(self, *, enable_backdrop: bool | None = None):
        super().__init__()
        self.setWindowTitle("Pixel Pilot")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.old_pos: QPoint | None = None
        self.click_through_enabled = False
        self.expanded = False
        self._background_hidden = False
        self.sidecar: Optional[SidecarPreview] = None
        self._backdrop_enabled = Config.GUI_ENABLE_GLASS_BACKDROP if enable_backdrop is None else bool(enable_backdrop)
        self.minimized_notch = MinimizedNotchWindow(enable_backdrop=self._backdrop_enabled)

        self.chat_widget = ChatWidget()
        self.setCentralWidget(self.chat_widget)
        self.setFixedSize(*self.BAR_SIZE)
        self.chat_widget.set_view_mode("bar_only")

        self.chat_widget.expand_btn.clicked.connect(self.toggle_expand)
        self.chat_widget.minimize_btn.clicked.connect(self.minimize_to_background)
        self.chat_widget.close_btn.clicked.connect(QApplication.quit)
        self.chat_widget.agent_view_visibility_changed.connect(self._refresh_sidecar_visibility)

        self._backdrop_controller: BackdropController | None = None
        if self._backdrop_enabled:
            self._backdrop_controller = BackdropController(
                self,
                excluded_hwnds=self._glass_excluded_hwnds,
                steady_fps=8,
                burst_fps=12,
            )
            self.minimized_notch.set_excluded_hwnds_provider(self._glass_excluded_hwnds)
        self.chat_widget.attach_backdrop_controller(self._backdrop_controller)

        self.center_at_top()

    def _glass_excluded_hwnds(self) -> list[int]:
        handles: list[int] = []
        for widget in (self, self.minimized_notch, self.sidecar):
            if widget is None:
                continue
            try:
                hwnd = int(widget.winId())
            except Exception:
                continue
            if hwnd:
                handles.append(hwnd)
        return handles

    def ensure_sidecar(self):
        if self.sidecar is not None:
            return self.sidecar

        self.sidecar = SidecarPreview(
            self,
            width=Config.SIDECAR_PREVIEW_WIDTH,
            height=Config.SIDECAR_PREVIEW_HEIGHT,
            fps=Config.SIDECAR_PREVIEW_FPS,
        )
        return self.sidecar

    def _refresh_sidecar_visibility(self):
        if not self.sidecar:
            return

        should_show = bool(
            self.isVisible()
            and not self._background_hidden
            and self.chat_widget.can_toggle_agent_view()
            and self.chat_widget.should_show_agent_view()
        )
        if should_show:
            self.sidecar.show()
            self.sidecar.reattach()
        else:
            self.sidecar.hide()

    def set_click_through_enabled(self, enable: bool):
        enable = bool(enable)
        if not enable:
            self._remember_external_foreground_window()

        was_visible = self.isVisible()

        flags = self.windowFlags()
        if enable:
            flags |= Qt.WindowTransparentForInput
            self.setWindowOpacity(Config.GUI_TRANSPARENCY_LEVEL)
        else:
            flags &= ~Qt.WindowTransparentForInput
            self.setWindowOpacity(1.0)

        self.setWindowFlags(flags)
        self.click_through_enabled = bool(enable)
        if was_visible:
            self.show()
        if enable:
            self._restore_last_external_foreground_window()

    def _remember_external_foreground_window(self):
        import ctypes
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd and hwnd != int(self.winId()):
                self._last_external_hwnd = hwnd
        except Exception:
            pass

    def _restore_last_external_foreground_window(self):
        import ctypes
        try:
            if hasattr(self, '_last_external_hwnd') and self._last_external_hwnd:
                ctypes.windll.user32.SetForegroundWindow(self._last_external_hwnd)
        except Exception:
            pass

    def center_at_top(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + 30
        self.move(x, y)

    def _clamp_to_screen(self):
        screen = self.screen() or QGuiApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left()), max(geo.left(), geo.right() - self.width() + 1))
        y = min(max(self.y(), geo.top()), max(geo.top(), geo.bottom() - self.height() + 1))
        self.move(x, y)

    def _resize_for_state(self, *, expanded: bool):
        old_h = self.height()
        old_w = self.width()
        target_w, target_h = self.EXTENDED_SIZE if expanded else self.BAR_SIZE
        if target_w == old_w and target_h == old_h:
            return

        self.setFixedSize(target_w, target_h)
        self._clamp_to_screen()

    def set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        if self.expanded == expanded:
            self.chat_widget.set_expanded(expanded)
            return

        self.expanded = expanded
        self._resize_for_state(expanded=expanded)
        self.chat_widget.set_expanded(expanded)
        if self.sidecar and self.sidecar.isVisible():
            self.sidecar.reattach()

    def toggle_expand(self):
        if self._background_hidden:
            self.restore_from_background()
            self.set_expanded(True)
            return
        self.set_expanded(not self.expanded)

    def minimize_to_background(self):
        screen = self.screen() or QGuiApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        self._background_hidden = True
        if self.sidecar:
            self.sidecar.hide()
        self.hide()
        self.minimized_notch.show_for_screen(screen)
        QTimer.singleShot(0, self.chat_widget.ensure_notch_voice_mode)

    def restore_from_background(self):
        self._background_hidden = False
        self.minimized_notch.hide_notch()
        self.show()
        self.raise_()
        self.activateWindow()
        self._refresh_sidecar_visibility()

    def toggle_background_visibility(self):
        if self._background_hidden or not self.isVisible():
            self.restore_from_background()
        else:
            self.minimize_to_background()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = None

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.sidecar and self.sidecar.isVisible():
            self.sidecar.reattach()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.sidecar and self.sidecar.isVisible():
            self.sidecar.reattach()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_sidecar_visibility()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.sidecar:
            self.sidecar.hide()
        if not self._background_hidden:
            self.minimized_notch.hide_notch()

    def closeEvent(self, event):
        self.minimized_notch.hide_notch()
        self.minimized_notch.close()
        if self.sidecar:
            self.sidecar.close()
            self.sidecar = None
        super().closeEvent(event)
