from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
                               QLineEdit, QPushButton, QLabel, QFrame, QComboBox)
from PySide6.QtCore import Qt

class ChatWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setup_ui()
        self.apply_styles()
        self.send_btn.clicked.connect(self.send_message)
        self.input_field.returnPressed.connect(self.send_message)

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        
        # Header
        self.header = QFrame()
        self.header.setObjectName("header")
        h = QHBoxLayout(self.header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        
        self.logo = QLabel("TOLIN")
        self.logo.setObjectName("logo")
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["ADVISOR", "HELPER", "AUTO"])
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.setItemData(0, "Guides you with advice", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(1, "Helps execute tasks", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(2, "Full autonomous control", Qt.ItemDataRole.ToolTipRole)
        
        self.minimize_btn = QPushButton("−")
        self.minimize_btn.setObjectName("controlBtn")
        self.minimize_btn.setFixedSize(24, 24)
        
        self.expand_btn = QPushButton("⤢")
        self.expand_btn.setObjectName("controlBtn")
        self.expand_btn.setFixedSize(24, 24)
        
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(24, 24)
        
        h.addWidget(self.logo)
        h.addWidget(self.mode_combo)
        h.addStretch()
        h.addWidget(self.minimize_btn)
        h.addWidget(self.expand_btn)
        h.addWidget(self.close_btn)
        
        layout.addWidget(self.header)
        
        # Chat
        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chatDisplay")
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("// Ready")
        layout.addWidget(self.chat_display)
        
        # Input
        self.input_frame = QFrame()
        self.input_frame.setObjectName("inputFrame")
        i = QHBoxLayout(self.input_frame)
        i.setContentsMargins(0, 0, 0, 0)
        i.setSpacing(8)
        
        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("> Type a command...")
        
        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setObjectName("actionBtn")
        self.mic_btn.setFixedSize(28, 28)
        
        self.send_btn = QPushButton("→")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(28, 28)
        
        i.addWidget(self.input_field)
        i.addWidget(self.mic_btn)
        i.addWidget(self.send_btn)
        
        layout.addWidget(self.input_frame)
        self.setLayout(layout)

    def apply_styles(self):
        self.setStyleSheet("""
            QToolTip { background: #1a1a1a; color: #e5e5e5; border: 1px solid #262626; padding: 4px 8px; font: 11px 'Consolas'; }
            ChatWidget { background: #0d0d0d; border: 1px solid #262626; border-radius: 8px; }
            QFrame#header { background: transparent; }
            QLabel#logo { color: #10b981; font: bold 14px 'Consolas'; letter-spacing: 2px; }
            QComboBox#modeCombo { background: #1a1a1a; color: #a3a3a3; border: 1px solid #262626; border-radius: 4px; padding: 4px 8px; font: 11px 'Consolas'; min-width: 80px; }
            QComboBox#modeCombo::drop-down { border: none; width: 16px; }
            QComboBox#modeCombo::down-arrow { image: none; }
            QComboBox#modeCombo:hover { border-color: #404040; }
            QComboBox QAbstractItemView { background: #1a1a1a; color: #e5e5e5; selection-background-color: #262626; border: 1px solid #262626; }
            QPushButton#controlBtn { background: transparent; color: #6b7280; border: none; font: 14px 'Consolas'; }
            QPushButton#controlBtn:hover { color: #e5e5e5; }
            QPushButton#closeBtn { background: transparent; color: #6b7280; border: none; font: 16px 'Consolas'; }
            QPushButton#closeBtn:hover { color: #ef4444; }
            QTextEdit#chatDisplay { background: #0d0d0d; color: #d4d4d4; border: none; font: 13px 'Consolas'; padding: 8px; }
            QFrame#inputFrame { background: #1a1a1a; border: 1px solid #262626; border-radius: 6px; padding: 6px; }
            QLineEdit#inputField { background: transparent; color: #fafafa; border: none; font: 13px 'Consolas'; padding: 4px; }
            QPushButton#actionBtn { background: transparent; color: #6b7280; border: 1px solid #262626; border-radius: 4px; font-size: 12px; }
            QPushButton#actionBtn:hover { color: #10b981; border-color: #10b981; }
            QPushButton#sendBtn { background: #10b981; color: #0d0d0d; border: none; border-radius: 4px; font: bold 14px; }
            QPushButton#sendBtn:hover { background: #059669; }
        """)

    def send_message(self):
        text = self.input_field.text().strip()
        if text:
            self.chat_display.append(f'<span style="color:#6b7280">you:</span> {text}')
            self.input_field.clear()

    def display_message(self, sender, text):
        color = "#10b981" if sender == "tolin" else "#6b7280"
        self.chat_display.append(f'<span style="color:{color}">{sender}:</span> {text}')
