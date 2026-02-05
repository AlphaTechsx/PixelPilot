from PySide6.QtWidgets import QMainWindow, QApplication
from PySide6.QtCore import Qt
from .chat_widget import ChatWidget

class MainWindow(QMainWindow):
    FULL_SIZE = (420, 480)
    COMPACT_SIZE = (420, 240)
    MINI_SIZE = (150, 60)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixel Pilot")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.expanded = True
        self.minimized = False
        self.last_size = self.FULL_SIZE
        self.old_pos = None
        
        self.chat_widget = ChatWidget()
        self.setCentralWidget(self.chat_widget)
        self.setFixedSize(*self.FULL_SIZE)
        self.chat_widget.set_view_mode("full")
        
        self.chat_widget.expand_btn.clicked.connect(self.toggle_expand)
        self.chat_widget.minimize_btn.clicked.connect(self.toggle_minimize)
        self.chat_widget.close_btn.clicked.connect(QApplication.quit)
        
        self.center_at_top()

    def center_at_top(self):
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - self.width()) // 2, 30)

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
