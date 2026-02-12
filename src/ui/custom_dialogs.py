from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QHBoxLayout, QPushButton, QFrame, QWidget, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor

class BaseDialog(QDialog):
    def __init__(self, parent=None, title="", width=400):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setFixedWidth(width)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(20, 20, 20, 20)
        self.container_layout.setSpacing(15)
        
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("titleLabel")
        self.container_layout.addWidget(self.title_label)
        
        self.layout.addWidget(self.container)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)
        
        self._apply_base_styles()
        
        self.old_pos = None

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

    def _apply_base_styles(self):
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QFrame#container {
                background: rgba(18, 30, 44, 245);
                border: 1px solid rgba(52, 78, 102, 170);
                border-radius: 12px;
            }
            QLabel#titleLabel {
                color: #057FCA;
                font: 700 12px 'Segoe UI', 'Inter', sans-serif;
                letter-spacing: 1px;
            }
            QLabel#messageLabel {
                color: #e5e5e5;
                font: 14px 'Segoe UI', 'Inter', sans-serif;
                line-height: 1.4;
            }
            QLineEdit#inputField {
                background: rgba(24, 40, 56, 175);
                border: 1px solid rgba(52, 78, 102, 160);
                border-radius: 6px;
                color: #ffffff;
                font: 14px 'Segoe UI', 'Inter', sans-serif;
                padding: 8px;
                selection-background-color: #057FCA;
            }
            QLineEdit#inputField:focus {
                border-color: #057FCA;
                background: rgba(30, 50, 70, 190);
            }
            QPushButton {
                padding: 6px 16px;
                border-radius: 6px;
                font: 600 13px 'Segoe UI', 'Inter', sans-serif;
                min-width: 80px;
            }
            QPushButton#primaryBtn {
                background: #057FCA;
                color: white;
                border: none;
            }
            QPushButton#primaryBtn:hover {
                background: #0469a6;
            }
            QPushButton#primaryBtn:pressed {
                background: #035485;
            }
            QPushButton#secondaryBtn {
                background: transparent;
                color: #8fb7d6;
                border: 1px solid rgba(52, 78, 102, 100);
            }
            QPushButton#secondaryBtn:hover {
                color: white;
                background: rgba(255, 255, 255, 0.05);
                border-color: rgba(52, 78, 102, 180);
            }
            QPushButton#secondaryBtn:pressed {
                background: rgba(255, 255, 255, 0.1);
            }
        """)

class ClarificationDialog(BaseDialog):
    def __init__(self, parent=None, title="Clarification Needed", question=""):
        super().__init__(parent, title)
        
        self.question_label = QLabel(question)
        self.question_label.setObjectName("messageLabel")
        self.question_label.setWordWrap(True)
        self.container_layout.addWidget(self.question_label)
        
        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("Type your answer...")
        self.container_layout.addWidget(self.input_field)
        
        self.button_layout = QHBoxLayout()
        self.button_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondaryBtn")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.submit_btn = QPushButton("Submit")
        self.submit_btn.setObjectName("primaryBtn")
        self.submit_btn.clicked.connect(self.accept)
        self.submit_btn.setDefault(True)
        
        self.button_layout.addWidget(self.cancel_btn)
        self.button_layout.addWidget(self.submit_btn)
        self.container_layout.addLayout(self.button_layout)

    def get_text(self):
        return self.input_field.text().strip()

class ConfirmationDialog(BaseDialog):
    def __init__(self, parent=None, title="Confirm Action", text="Are you sure?"):
        super().__init__(parent, title)
        
        self.label = QLabel(text)
        self.label.setObjectName("messageLabel")
        self.label.setWordWrap(True)
        self.container_layout.addWidget(self.label)
        
        self.button_layout = QHBoxLayout()
        self.button_layout.addStretch()
        
        self.no_btn = QPushButton("No")
        self.no_btn.setObjectName("secondaryBtn")
        self.no_btn.clicked.connect(self.reject)
        
        self.yes_btn = QPushButton("Yes")
        self.yes_btn.setObjectName("primaryBtn")
        self.yes_btn.clicked.connect(self.accept)
        self.yes_btn.setDefault(True)
        
        self.button_layout.addWidget(self.no_btn)
        self.button_layout.addWidget(self.yes_btn)
        self.container_layout.addLayout(self.button_layout)

class MessageDialog(BaseDialog):
    def __init__(self, parent=None, title="Info", text=""):
        super().__init__(parent, title)
        
        self.label = QLabel(text)
        self.label.setObjectName("messageLabel")
        self.label.setWordWrap(True)
        self.container_layout.addWidget(self.label)
        
        self.button_layout = QHBoxLayout()
        self.button_layout.addStretch()
        
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("primaryBtn")
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setDefault(True)
        
        self.button_layout.addWidget(self.ok_btn)
        self.container_layout.addLayout(self.button_layout)
