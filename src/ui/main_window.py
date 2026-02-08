from typing import Optional

from PySide6.QtWidgets import QMainWindow, QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QCursor
from .chat_widget import ChatWidget
from config import Config


class MainWindow(QMainWindow):
    FULL_SIZE = (460, 480)
    COMPACT_SIZE = (460, 240)
    MINI_SIZE = (160, 60)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixel Pilot")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.expanded = True
        self.minimized = False
        self.last_size = self.FULL_SIZE
        self.old_pos = None

        self.click_through_enabled = False
        
        # Sidecar preview (set by controller)
        self.sidecar: Optional["SidecarPreview"] = None
        
        self.chat_widget = ChatWidget()
        self.setCentralWidget(self.chat_widget)
        self.setFixedSize(*self.FULL_SIZE)
        self.chat_widget.set_view_mode("full")
        
        self.chat_widget.expand_btn.clicked.connect(self.toggle_expand)
        self.chat_widget.minimize_btn.clicked.connect(self.toggle_minimize)
        self.chat_widget.close_btn.clicked.connect(QApplication.quit)
        
        self.center_at_top()

    def set_click_through_enabled(self, enable: bool):
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

    def center_at_top(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + 30
        self.move(x, y)

    def toggle_minimize(self):
        if not self.minimized:
            self.last_size = (self.width(), self.height())
            self.setFixedSize(*self.MINI_SIZE)
            self.chat_widget.set_view_mode("mini")
            self.chat_widget.expand_btn.hide()
            self.chat_widget.minimize_btn.setText("+")
            self.minimized = True
        else:
            self.setFixedSize(*self.last_size)
            if self.expanded:
                self.chat_widget.set_view_mode("full")
            else:
                self.chat_widget.set_view_mode("compact")
            self.chat_widget.expand_btn.show()
            self.chat_widget.minimize_btn.setText("−")
            self.minimized = False

    def toggle_expand(self):
        if self.minimized:
            return
        if self.expanded:
            self.setFixedSize(*self.COMPACT_SIZE)
            self.expanded = False
            self.chat_widget.set_view_mode("compact")
        else:
            self.setFixedSize(*self.FULL_SIZE)
            self.expanded = True
            self.chat_widget.set_view_mode("full")

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
        """Reposition sidecar when main window moves."""
        super().moveEvent(event)
        if self.sidecar:
            self.sidecar.reattach()

    def resizeEvent(self, event):
        """Reposition sidecar when main window is resized."""
        super().resizeEvent(event)
        if self.sidecar:
            self.sidecar.reattach()

    def showEvent(self, event):
        """Show sidecar when main window is shown."""
        super().showEvent(event)
        if self.sidecar:
            if self.sidecar.isVisible():
                self.sidecar.reattach()

    def hideEvent(self, event):
        """Hide sidecar when main window is hidden."""
        super().hideEvent(event)
        if self.sidecar:
            self.sidecar.hide()

    def closeEvent(self, event):
        """Clean up sidecar when closing."""
        if self.sidecar:
            self.sidecar.close()
        super().closeEvent(event)
