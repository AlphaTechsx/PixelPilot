import sys
import logging
import ctypes
from typing import TYPE_CHECKING, List, Dict

from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QPushButton, QLabel

if TYPE_CHECKING:
    from desktop.desktop_manager import AgentDesktopManager

logger = logging.getLogger("pixelpilot.shell")

class IconButton(QWidget):
    """A desktop icon with a label."""
    def __init__(self, name, icon_char, cmd, desktop_manager):
        super().__init__()
        self.cmd = cmd
        self.dm = desktop_manager
        self.setFixedSize(80, 90)
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.icon_label = QLabel(icon_char)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 32px; color: #4e4e8a; background: transparent;")
        
        self.text_label = QLabel(name)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setStyleSheet("font-size: 11px; color: white; background: transparent;")
        self.text_label.setWordWrap(True)
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dm.launch_process(self.cmd)

class DesktopBackground(QWidget):
    """A simple fullscreen widget to provide a desktop background with icons."""
    def __init__(self, desktop_manager):
        super().__init__(None)
        self.setWindowTitle("AgentDesktopWallpaper")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background-color: #0f0f1b;") # Dark space theme
        
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        
        # Icon Grid
        self.layout = QGridLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(20)
        
        icons = [
            ("Command\nPrompt", "💻", "cmd.exe"),
            ("Notepad", "📝", "notepad.exe"),
            ("File\nExplorer", "📁", "explorer.exe"),
            ("Task\nManager", "⚙️", "taskmgr.exe"),
        ]
        
        for i, (name, icon, cmd) in enumerate(icons):
            row = i % 8
            col = i // 8
            btn = IconButton(name, icon, cmd, desktop_manager)
            self.layout.addWidget(btn, row, col)

class MinimalTaskbar(QWidget):
    """
    A minimal, always-on-top taskbar for the Agent Desktop.
    Lists open windows and provides a basic "Session" feel.
    """
    def __init__(self, desktop_manager: "AgentDesktopManager"):
        super().__init__(None)
        self.setWindowTitle("AgentDesktopTaskbar")
        self.desktop_manager = desktop_manager
        
        # Initialize background first
        self.background = DesktopBackground(desktop_manager)
        self.background.show()
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint 
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                border-top: 1px solid #4a4a6a;
                color: white;
            }
            QPushButton {
                background-color: #2a2a4e;
                border: 1px solid #4a4a6a;
                padding: 5px 10px;
                border_radius: 3px;
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3a3a6e;
            }
            QPushButton#startBtn {
                background-color: #4e4e8a;
                font-weight: bold;
            }
            QLabel {
                padding: 0 10px;
                font-weight: bold;
                color: #8a8ab0;
                background: transparent;
            }
        """)
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5, 2, 5, 2)
        self.layout.setSpacing(5)
        
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedWidth(60)
        self.start_btn.clicked.connect(self.show_launcher)
        self.layout.addWidget(self.start_btn)
        
        self.label = QLabel("|")
        self.layout.addWidget(self.label)
        
        self.windows_container = QWidget()
        self.windows_layout = QHBoxLayout(self.windows_container)
        self.windows_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.windows_container)
        self.layout.addStretch()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_windows)
        self.timer.start(2000) # Refresh every 2 seconds
        
        self._update_geometry()
        
    def show_launcher(self):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1a1a2e; border: 1px solid #4a4a6a; color: white; padding: 5px; }
            QMenu::item:selected { background-color: #4a4a8a; }
        """)
        
        apps = [
            ("Command Prompt", "cmd.exe"),
            ("Notepad", "notepad.exe"),
            ("Task Manager", "taskmgr.exe"),
            ("Calculator", "calc.exe"),
            ("File Explorer", "explorer.exe")
        ]
        
        for name, cmd in apps:
            action = QAction(name, self)
            action.triggered.connect(lambda checked=False, c=cmd: self.desktop_manager.launch_process(c))
            menu.addAction(action)
            
        # Position menu above the start button
        menu_pos = self.mapToGlobal(self.start_btn.rect().topLeft())
        menu.exec(menu_pos - QPoint(0, menu.sizeHint().height()))

    def _update_geometry(self):
        # Position at the bottom of the screen
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(0, screen.height() - 40, screen.width(), 40)

    def update_windows(self):
        # Clear existing buttons
        for i in reversed(range(self.windows_layout.count())): 
            widget = self.windows_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
            
        try:
            windows = self.desktop_manager.list_windows()
            # Filter out shell components
            filtered = [w for w in windows if w['title'] and "AgentDesktop" not in w['title']]
            
            for w in filtered[:8]: # Limit to 8 buttons
                btn = QPushButton(w['title'][:20] + ("..." if len(w['title']) > 20 else ""))
                btn.clicked.connect(lambda checked=False, hwnd=w['hwnd']: self.activate_window(hwnd))
                self.windows_layout.addWidget(btn)
        except Exception as e:
            logger.debug(f"Taskbar update error: {e}")

    def activate_window(self, hwnd):
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 5) # SW_SHOW

def main():
    # Helper to launch standalone on the desktop
    import os
    import sys
    
    # Add src to sys.path to allow absolute imports
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    
    from desktop.desktop_manager import AgentDesktopManager
    
    desktop_name = os.environ.get("AGENT_DESKTOP_NAME", "PixelPilotAgent")
    dm = AgentDesktopManager(desktop_name)
    
    app = QApplication(sys.argv)
    bar = MinimalTaskbar(dm)
    bar.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
