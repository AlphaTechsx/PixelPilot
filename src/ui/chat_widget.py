import os

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                               QLineEdit, QPushButton, QLabel, QFrame, QComboBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtSvgWidgets import QSvgWidget

from services.audio import AudioService
from .voice_visualizer import VoiceVisualizer

class ChatWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.audio_service = AudioService()
        self.audio_service.text_received.connect(self.on_speech_text)
        self.audio_service.status_changed.connect(self.on_listening_status)
        self.audio_service.level_changed.connect(self.on_audio_level)
        self.setup_ui()
        self.apply_styles()
        self.send_btn.clicked.connect(self.send_message)
        self.input_field.returnPressed.connect(self.send_message)
        self.mic_btn.clicked.connect(self.toggle_listening)
        self.mode_combo.currentIndexChanged.connect(self.update_mode_tooltip)
        self.view_mode = "full"
        self.set_view_mode("full")
        self.update_mode_tooltip()

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
        
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logo", "pixelpilot-icon.svg"))
        self.logo = QSvgWidget(logo_path)
        self.logo.setObjectName("logo")
        self.logo.setFixedSize(50, 50)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["GUIDANCE", "AUTO"])
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.setItemData(0, "Step-by-step guidance with continuous human input.", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(1, "Minimal interaction. PIXIE runs tasks end-to-end.", Qt.ItemDataRole.ToolTipRole)
        
        self.minimize_btn = QPushButton("−")
        self.minimize_btn.setObjectName("minimizeBtn")
        self.minimize_btn.setFixedSize(28, 28)
        self.minimize_btn.setToolTip("Drift into the small")
        
        self.expand_btn = QPushButton("⤢")
        self.expand_btn.setObjectName("expandBtn")
        self.expand_btn.setFixedSize(28, 28)
        self.expand_btn.setToolTip("Expand the horizon")
        
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(28, 28)
        
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
        self.chat_display.setAcceptRichText(True)
        self.chat_display.setPlaceholderText("Ask anything to Pixie.")
        layout.addWidget(self.chat_display)

        # Visualizer
        self.voice_visualizer = VoiceVisualizer()
        self.voice_visualizer.setObjectName("voiceVisualizer")
        self.voice_visualizer.setVisible(False)
        layout.addWidget(self.voice_visualizer)

        # Compact stop button (not in header)
        self.compact_stop_btn = QPushButton("Stop")
        self.compact_stop_btn.setObjectName("compactStopBtn")
        self.compact_stop_btn.setFixedHeight(26)
        self.compact_stop_btn.setVisible(False)
        self.compact_stop_btn.setToolTip("Stop voice session")
        self.compact_stop_btn.clicked.connect(self.audio_service.stop_listening)
        layout.addWidget(self.compact_stop_btn)
        
        # Input
        self.input_hint = QLabel("Open apps, send emails/WhatsApp, fix PC issues, or ask anything…")
        self.input_hint.setObjectName("inputHint")
        self.input_hint.setWordWrap(True)
        layout.addWidget(self.input_hint)

        self.input_frame = QFrame()
        self.input_frame.setObjectName("inputFrame")
        i = QHBoxLayout(self.input_frame)
        i.setContentsMargins(0, 0, 0, 0)
        i.setSpacing(8)
        
        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("> Type a command...")
        
        self.mic_btn = QPushButton("🎙")
        self.mic_btn.setObjectName("micBtn")
        self.mic_btn.setFixedSize(34, 34)
        self.mic_btn.setToolTip("Start listening")
        
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
            QToolTip { background: #1a1a1a; color: #e5e5e5; border: 1px solid #262626; padding: 6px 10px; font: 11px 'Segoe UI', 'Inter', sans-serif; }
            ChatWidget { background: rgba(18, 30, 44, 190); border: 1px solid rgba(52, 78, 102, 170); border-radius: 10px; font-family: 'Segoe UI', 'Inter', sans-serif; }
            QFrame#header { background: transparent; }
            QLabel#logo { color: #057FCA; font: bold 14px 'Consolas'; letter-spacing: 2px; }
            QComboBox#modeCombo { background: rgba(20, 36, 54, 180); color: #cfe9ff; border: 1px solid rgba(52, 78, 102, 180); border-radius: 8px; padding: 6px 12px; font: 600 12px 'Segoe UI', 'Inter', sans-serif; letter-spacing: 0.4px; min-width: 104px; }
            QComboBox#modeCombo::drop-down { border: none; width: 16px; }
            QComboBox#modeCombo::down-arrow { image: none; }
            QComboBox#modeCombo:hover { border-color: #404040; }
            QComboBox QAbstractItemView { background: rgba(20, 36, 54, 210); color: #e5f3ff; selection-background-color: rgba(36, 60, 86, 190); border: 1px solid rgba(52, 78, 102, 180); font: 500 11px 'Segoe UI', 'Inter', sans-serif; }
            QPushButton#minimizeBtn, QPushButton#expandBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b1f2a, stop:1 #0f2f43);
                color: #bfe6ff;
                border: 1px solid #1b3c52;
                border-radius: 8px;
                font: 700 12px 'Segoe UI', 'Inter', sans-serif;
                padding: 2px;
            }
            QPushButton#minimizeBtn:hover, QPushButton#expandBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0e2b3a, stop:1 #144763);
                border-color: #057FCA;
                color: #e9f6ff;
            }
            QPushButton#minimizeBtn:pressed, QPushButton#expandBtn:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0a1a24, stop:1 #0b2a3a);
                border-color: #0a5f97;
            }
            QPushButton#closeBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2a0b0b, stop:1 #3a0b14);
                color: #ffd1d1;
                border: 1px solid #4a1b1b;
                border-radius: 8px;
                font: 700 12px 'Segoe UI', 'Inter', sans-serif;
                padding: 2px;
            }
            QPushButton#closeBtn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3a0f0f, stop:1 #4a1018); color: #ffffff; border-color: #ef4444; }
            QPushButton#closeBtn:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #220909, stop:1 #300810); border-color: #b91c1c; }
            QTextEdit#chatDisplay { background: rgba(20, 34, 50, 170); color: #d4d4d4; border: none; font: 15px 'Segoe UI', 'Inter', sans-serif; padding: 8px; }
            QLabel#inputHint { color: #8fb7d6; font: 12px 'Segoe UI', 'Inter', sans-serif; padding: 2px 4px; }
            QWidget#voiceVisualizer { background: rgba(18, 32, 48, 165); border: 1px solid rgba(46, 72, 96, 170); border-radius: 10px; }
            QPushButton#compactStopBtn { background: rgba(12, 24, 36, 170); color: #bfe6ff; border: 1px solid rgba(52, 78, 102, 170); border-radius: 8px; font: 700 11px 'Segoe UI', 'Inter', sans-serif; letter-spacing: 0.3px; padding: 4px 10px; }
            QPushButton#compactStopBtn:hover { background: rgba(14, 30, 46, 190); border-color: #057FCA; color: #e9f6ff; }
            QPushButton#compactStopBtn:pressed { background: rgba(10, 22, 32, 190); border-color: #0a5f97; }
            QFrame#inputFrame { background: rgba(24, 40, 56, 175); border: 1px solid rgba(52, 78, 102, 160); border-radius: 8px; padding: 6px; }
            QLineEdit#inputField { background: transparent; color: #fafafa; border: none; font: 14px 'Segoe UI', 'Inter', sans-serif; padding: 4px; }
            QPushButton#micBtn {
                background: qradialgradient(cx:0.3, cy:0.3, radius:1.1, stop:0 #0c3b5a, stop:1 #0a2233);
                color: #d7efff;
                border: 1px solid rgba(52, 78, 102, 180);
                border-radius: 10px;
                font: 700 12px 'Segoe UI', 'Inter', sans-serif;
            }
            QPushButton#micBtn:hover { border-color: #057FCA; color: #ffffff; }
            QPushButton#micBtn:pressed { background: qradialgradient(cx:0.4, cy:0.4, radius:1.1, stop:0 #0a2f45, stop:1 #081a26); }
            QPushButton#sendBtn { background: #057FCA; color: #0d0d0d; border: none; border-radius: 4px; font: 700 14px 'Segoe UI', 'Inter', sans-serif; letter-spacing: 0.2px; }
            QPushButton#sendBtn:hover { background: #059669; }
        """)

    def send_message(self):
        text = self.input_field.text().strip()
        if text:
            self._hide_input_hint()
            self.display_message("you", text)
            self.input_field.clear()
            self.send_to_agent(text)

    def display_message(self, sender, text):
        sender_key = sender.lower()
        is_user = sender_key == "you"
        bubble_bg = QColor("#0f2f43" if is_user else "#0c1f2f")
        text_color = QColor("#e6f3ff")
        viewport_width = max(300, self.chat_display.viewport().width())
        max_width = int(viewport_width * 0.7)
        side_margin = max(10, viewport_width - max_width)

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        block_format = QTextBlockFormat()
        block_format.setAlignment(Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft)
        if is_user:
            block_format.setLeftMargin(side_margin)
            block_format.setRightMargin(0)
        else:
            block_format.setLeftMargin(0)
            block_format.setRightMargin(side_margin)

        char_format = QTextCharFormat()
        char_format.setForeground(text_color)
        char_format.setBackground(bubble_bg)

        cursor.insertBlock(block_format)
        cursor.insertText(text, char_format)
        cursor.insertBlock()

        self.chat_display.setTextCursor(cursor)
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())

    def toggle_listening(self):
        if self.audio_service.is_listening:
            self.audio_service.stop_listening()
        else:
            self.audio_service.start_listening()

    def on_listening_status(self, listening):
        if listening:
            self.mic_btn.setText("■")
            self.mic_btn.setToolTip("Stop listening")
            self.input_field.setPlaceholderText("> Listening...")
        else:
            self.mic_btn.setText("🎤")
            self.mic_btn.setToolTip("Start listening")
            self.input_field.setPlaceholderText("> Type a command...")
        self._apply_view_mode()

    def on_speech_text(self, text):
        if not text:
            return
        self._hide_input_hint()
        self.display_message("you", text)
        self.send_to_agent(text)

    def on_audio_level(self, level):
        self.voice_visualizer.set_level(level)

    def send_to_agent(self, text):
        # TODO: Replace with real agent call
        self.display_message("pixie", f"(dummy) Received: {text}")

    def update_mode_tooltip(self):
        tip = self.mode_combo.itemData(self.mode_combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        if tip:
            self.mode_combo.setToolTip(tip)

    def set_view_mode(self, mode):
        self.view_mode = mode
        if mode == "mini":
            if self.audio_service.is_listening:
                self.audio_service.stop_listening()
        self._apply_view_mode()

    def _apply_view_mode(self):
        listening = self.audio_service.is_listening
        if self.view_mode == "mini":
            self.chat_display.hide()
            self.input_frame.hide()
            self.input_hint.hide()
            self.mode_combo.hide()
            self.voice_visualizer.hide()
            self.voice_visualizer.set_active(False)
            self.compact_stop_btn.hide()
            return

        if self.view_mode == "compact":
            self.mode_combo.hide()
            if listening:
                self.chat_display.hide()
                self.input_frame.hide()
                self.input_hint.hide()
                self.voice_visualizer.show()
                self.voice_visualizer.set_active(True)
                self.compact_stop_btn.show()
            else:
                self.chat_display.show()
                self.input_frame.show()
                if self.chat_display.toPlainText().strip():
                    self.input_hint.hide()
                else:
                    self.input_hint.show()
                self.voice_visualizer.hide()
                self.voice_visualizer.set_active(False)
                self.compact_stop_btn.hide()
            return

        # full
        self.chat_display.show()
        self.input_frame.show()
        if self.chat_display.toPlainText().strip():
            self.input_hint.hide()
        else:
            self.input_hint.show()
        self.mode_combo.show()
        self.compact_stop_btn.hide()
        if listening:
            self.voice_visualizer.show()
            self.voice_visualizer.set_active(True)
        else:
            self.voice_visualizer.hide()
            self.voice_visualizer.set_active(False)

    def _hide_input_hint(self):
        if self.input_hint.isVisible():
            self.input_hint.hide()
