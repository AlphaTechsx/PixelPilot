"""
Sidecar Preview Widget

Displays a live, read-only preview of the Agent Desktop attached to
the right edge of the main PixelPilot window.
"""

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer, Qt, QPoint, QObject, Signal, QThread, QSize, Slot
from PySide6.QtGui import QPixmap, QImage, QGuiApplication
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QSizeGrip, QFrame

if TYPE_CHECKING:
    from desktop.desktop_manager import AgentDesktopManager
    from PySide6.QtWidgets import QMainWindow

logger = logging.getLogger("pixelpilot.sidecar")


class CaptureWorker(QObject):
    """Worker to capture and process desktop frames in a background thread."""
    image_captured = Signal(QPixmap)

    def __init__(self, desktop_manager: "AgentDesktopManager"):
        super().__init__()
        self.desktop_manager = desktop_manager
        self.target_size = QSize(400, 300)

    @Slot()
    def process_frame(self):
        """Capture frame, scale it, and emit the result."""
        if not self.desktop_manager or not self.desktop_manager.is_created:
            return

        try:
            raw_data = self.desktop_manager.capture_desktop_raw()
            if not raw_data:
                return
                
            data, width, height = raw_data

            qimage = QImage(data, width, height, QImage.Format.Format_ARGB32).copy()
            
            pixmap = QPixmap.fromImage(qimage)
            
            scaled = pixmap.scaled(
                self.target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_captured.emit(scaled)
        except Exception as e:
            logger.debug(f"Worker capture error: {e}")


class EmbeddedAgentPreview(QFrame):
    """
    Embedded, in-window agent desktop preview used by the expanded panel.

    Unlike SidecarPreview, this widget does not create its own top-level window.
    """

    request_capture = Signal()

    def __init__(self, parent: Optional[QWidget] = None, fps: int = 10):
        super().__init__(parent)
        self.fps = max(1, int(fps or 10))
        self.desktop_manager: Optional["AgentDesktopManager"] = None
        self._active = False

        self.capture_thread = QThread()
        self.worker: Optional[CaptureWorker] = None

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        self.setObjectName("embeddedAgentPreview")
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.preview_label = QLabel("Agent view unavailable")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(180)
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background: #111827;
                color: #93a3b8;
                border: 1px solid #263447;
                border-radius: 12px;
                padding: 10px;
            }
            """
        )
        layout.addWidget(self.preview_label)

    def _setup_timer(self):
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._request_frame)
        self.refresh_timer.setInterval(int(1000 / self.fps))

    def _teardown_worker(self):
        if self.worker is not None:
            try:
                self.request_capture.disconnect(self.worker.process_frame)
            except Exception:
                pass
            try:
                self.worker.image_captured.disconnect(self._update_preview)
            except Exception:
                pass
            self.worker = None

        if self.capture_thread.isRunning():
            self.capture_thread.quit()
            self.capture_thread.wait()

    def set_capture_source(self, desktop_manager: Optional["AgentDesktopManager"]):
        self.desktop_manager = desktop_manager
        self._teardown_worker()

        if not desktop_manager:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Agent view unavailable")
            self.refresh_timer.stop()
            return

        self.worker = CaptureWorker(desktop_manager)
        self.worker.moveToThread(self.capture_thread)
        self.worker.image_captured.connect(self._update_preview)
        self.request_capture.connect(self.worker.process_frame)
        self.capture_thread.start()

        if self._active and self.isVisible():
            self.refresh_timer.start()

    def set_active(self, active: bool):
        self._active = bool(active)
        if not self._active:
            self.refresh_timer.stop()
            return

        if self.desktop_manager and self.isVisible():
            self.refresh_timer.start()

    def _request_frame(self):
        if not self.worker or not self.desktop_manager:
            return
        self.worker.target_size = self.preview_label.size()
        self.request_capture.emit()

    @Slot(QPixmap)
    def _update_preview(self, pixmap: QPixmap):
        self.preview_label.setText("")
        self.preview_label.setPixmap(pixmap)

    def showEvent(self, event):
        super().showEvent(event)
        if self._active and self.desktop_manager:
            self.refresh_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.refresh_timer.stop()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        self._teardown_worker()
        super().closeEvent(event)


class SidecarPreview(QWidget):
    """
    A frameless, read-only preview window that shows the Agent Desktop.

    The sidecar:
    - Attaches to the right edge of the main window
    - Updates at configurable FPS (default 5)
    - Ignores all mouse/keyboard input
    - Scales the desktop capture to fit preview size
    """

    request_capture = Signal()

    DEFAULT_WIDTH = 400
    DEFAULT_HEIGHT = 300
    DEFAULT_FPS = 30

    def __init__(
        self,
        parent_window: "QMainWindow",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
    ):
        super().__init__(None) 

        self.parent_window = parent_window
        self.preview_width = width
        self.fps = fps
        self.desktop_manager: Optional["AgentDesktopManager"] = None
        self._is_resizing = False
        self._initial_size_locked = True
        self._suppress_resize_once = False

        self.aspect_ratio = self._detect_aspect_ratio()

        self.preview_height = int(width / self.aspect_ratio)

        self._setup_worker()
        self._setup_ui()
        self._setup_timer()

    def _setup_worker(self):
        """Initialize the background capture worker."""
        self.capture_thread = QThread()
        self.worker = None

    def _detect_aspect_ratio(self) -> float:
        screen = None
        if self.parent_window:
            try:
                screen = self.parent_window.screen()
            except Exception:
                screen = None

        if screen is None:
            screen = QGuiApplication.primaryScreen()

        try:
            if screen:
                geo = screen.availableGeometry()
                if geo.height() > 0:
                    return geo.width() / geo.height()
        except Exception:
            pass

        try:
            from ctypes import windll
            scr_w = windll.user32.GetSystemMetrics(0)
            scr_h = windll.user32.GetSystemMetrics(1)
            if scr_h > 0:
                return scr_w / scr_h
        except Exception:
            pass

        return 16 / 9

    def _setup_ui(self):
        """Configure the widget appearance."""
        self.setWindowTitle("Agent Desktop Preview")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setFixedSize(self.preview_width, self.preview_height)
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                border: 2px solid #4a4a6a;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #0d0d1a;
                border-radius: 4px;
                color: #666688;
            }
        """)
        self.preview_label.setText("Agent Desktop\n(Waiting...)")
        layout.addWidget(self.preview_label)

        self.size_grip = QSizeGrip(self)
        self.size_grip.resize(16, 16)
        self.size_grip.raise_()

    def _setup_timer(self):
        """Setup the refresh timer."""
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._request_frame)
        interval_ms = int(1000 / self.fps) if self.fps > 0 else 200
        self.refresh_timer.setInterval(interval_ms)

    def _update_preview_geometry(self):
        layout = self.layout()
        if not layout:
            return

        margins = layout.contentsMargins()
        grip_size = self.size_grip.sizeHint()

        available_w = max(0, self.width() - margins.left() - margins.right())
        available_h = max(0, self.height() - margins.top() - margins.bottom())
        label_w = max(0, available_w)
        label_h = max(0, available_h - grip_size.height())

        self.preview_label.setFixedSize(label_w, label_h)

        grip_w = grip_size.width()
        grip_h = grip_size.height()
        self.size_grip.resize(grip_size)
        self.size_grip.move(
            self.width() - grip_w - margins.right(),
            self.height() - grip_h - margins.bottom(),
        )

    def _release_initial_size_lock(self):
        if not self._initial_size_locked:
            return

        self._initial_size_locked = False
        self._suppress_resize_once = True
        self.setMinimumSize(200, 150)
        self.setMaximumSize(QSize(16777215, 16777215))
        self.resize(self.preview_width, self.preview_height)
        self._update_preview_geometry()

    def _request_frame(self):
        """Request the worker to process a frame."""
        if self.worker:
            self.worker.target_size = self.preview_label.size()
            self.request_capture.emit()

    @Slot(QPixmap)
    def _update_preview(self, pixmap: QPixmap):
        """Update the UI with the captured pixmap."""
        self.preview_label.setPixmap(pixmap)

    def set_capture_source(self, desktop_manager: "AgentDesktopManager"):
        """
        Set the desktop manager to capture from.

        Args:
            desktop_manager: The AgentDesktopManager instance.
        """
        self.desktop_manager = desktop_manager
        
        if self.worker:
            self.capture_thread.quit()
            self.capture_thread.wait()

        self.worker = CaptureWorker(desktop_manager)
        self.worker.moveToThread(self.capture_thread)
        self.worker.image_captured.connect(self._update_preview)
        self.request_capture.connect(self.worker.process_frame)
        self.capture_thread.start()

        if self.isVisible():
            self.refresh_timer.start()

    def attach_to_window(self):
        """Position the sidecar to the right of the parent window."""
        if not self.parent_window:
            return

        parent_geo = self.parent_window.geometry()
        new_x = parent_geo.right() + 10
        new_y = parent_geo.top()

        self.move(new_x, new_y)

    def reattach(self):
        """Reposition after parent window moves/resizes."""
        self.attach_to_window()

    def resizeEvent(self, event):
        """Maintain aspect ratio during resize."""
        if self._suppress_resize_once:
            self._suppress_resize_once = False
            super().resizeEvent(event)
            self._update_preview_geometry()
            return
        if self._is_resizing:
            super().resizeEvent(event)
            return

        self._is_resizing = True
        try:
            new_size = event.size()
            w = new_size.width()
            h = new_size.height()
            
            old_size = event.oldSize()
            if old_size.width() > 0 and old_size.height() > 0:
                dw = abs(w - old_size.width())
                dh = abs(h - old_size.height())
                
                if dw > dh:
                    h = int(w / self.aspect_ratio)
                else:
                    w = int(h * self.aspect_ratio)
            else:
                h = int(w / self.aspect_ratio)
            
            if w != new_size.width() or h != new_size.height():
                self.resize(w, h)
            
            super().resizeEvent(event)
            self._update_preview_geometry()
        finally:
            self._is_resizing = False

    def showEvent(self, event):
        """Handle show event."""
        super().showEvent(event)
        self.attach_to_window()
        self._update_preview_geometry()
        QTimer.singleShot(0, self._release_initial_size_lock)
        if self.desktop_manager:
            self.refresh_timer.start()

    def hideEvent(self, event):
        """Handle hide event."""
        super().hideEvent(event)
        self.refresh_timer.stop()

    def close(self):
        """Clean up resources."""
        self.refresh_timer.stop()
        if self.capture_thread:
            self.capture_thread.quit()
            self.capture_thread.wait()
        super().close()
