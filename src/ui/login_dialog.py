"""
Login dialog for user authentication.
Shows email/password fields with login and register options.
"""

import os
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

from auth_manager import get_auth_manager


class LoginDialog(QDialog):
    """Login/Register dialog for user authentication."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.auth_manager = get_auth_manager()
        self.success = False
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        self.setWindowTitle("PixelPilot - Login")
        self.setFixedSize(420, 700)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.container = QFrame(self)
        self.container.setObjectName("container")
        self.container.setGeometry(0, 0, 420, 700)

        layout = QVBoxLayout(self.container)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 28, 32, 28)

        logo_container = QFrame()
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setAlignment(Qt.AlignCenter)

        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "logos",
            "pixelpilot-icon.svg",
        )
        if os.path.exists(logo_path):
            renderer = QSvgRenderer(logo_path)
            if renderer.isValid():
                pixmap = QPixmap(50, 50)
                pixmap.fill(QColor("transparent"))
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                logo_label = QLabel()
                logo_label.setPixmap(pixmap)
                logo_layout.addWidget(logo_label)

        layout.addWidget(logo_container)
        layout.addSpacing(8)

        title = QLabel("Welcome Back")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Sign in to continue to PixelPilot")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        email_label = QLabel("Email")
        email_label.setObjectName("fieldLabel")
        layout.addWidget(email_label)

        self.email_input = QLineEdit()
        self.email_input.setObjectName("inputField")
        self.email_input.setPlaceholderText("Enter your email")
        self.email_input.setMinimumHeight(42)
        layout.addWidget(self.email_input)

        layout.addSpacing(8)

        password_label = QLabel("Password")
        password_label.setObjectName("fieldLabel")
        layout.addWidget(password_label)

        self.password_input = QLineEdit()
        self.password_input.setObjectName("inputField")
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumHeight(42)
        self.password_input.returnPressed.connect(self._on_login)
        layout.addWidget(self.password_input)

        layout.addSpacing(16)

        self.login_btn = QPushButton("Sign In")
        self.login_btn.setObjectName("primaryBtn")
        self.login_btn.setMinimumHeight(44)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self._on_login)
        layout.addWidget(self.login_btn)

        divider_container = QFrame()
        divider_layout = QHBoxLayout(divider_container)
        divider_layout.setContentsMargins(0, 8, 0, 8)

        left_line = QFrame()
        left_line.setObjectName("dividerLine")
        left_line.setFixedHeight(1)

        or_label = QLabel("or")
        or_label.setObjectName("dividerText")

        right_line = QFrame()
        right_line.setObjectName("dividerLine")
        right_line.setFixedHeight(1)

        divider_layout.addWidget(left_line, 1)
        divider_layout.addWidget(or_label)
        divider_layout.addWidget(right_line, 1)
        layout.addWidget(divider_container)

        self.register_btn = QPushButton("Create Account")
        self.register_btn.setObjectName("secondaryBtn")
        self.register_btn.setMinimumHeight(44)
        self.register_btn.setCursor(Qt.PointingHandCursor)
        self.register_btn.clicked.connect(self._on_register)
        layout.addWidget(self.register_btn)

        layout.addSpacing(16)

        # API Key Section
        api_key_label = QLabel("Or use your own API Key")
        api_key_label.setObjectName("dividerText")
        api_key_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(api_key_label)

        self.api_key_input = QLineEdit()
        self.api_key_input.setObjectName("inputField")
        self.api_key_input.setPlaceholderText("Paste Gemini API Key (starts with AIza...)")
        self.api_key_input.setMinimumHeight(42)
        self.api_key_input.returnPressed.connect(self._on_use_api_key)
        layout.addWidget(self.api_key_input)

        self.use_key_btn = QPushButton("Use API Key")
        self.use_key_btn.setObjectName("secondaryBtn")
        self.use_key_btn.setMinimumHeight(44)
        self.use_key_btn.setCursor(Qt.PointingHandCursor)
        self.use_key_btn.clicked.connect(self._on_use_api_key)
        layout.addWidget(self.use_key_btn)

        layout.addSpacing(8)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.close_btn = QPushButton("x", self.container)
        self.close_btn.setObjectName("closeDialogBtn")
        self.close_btn.setGeometry(380, 12, 28, 28)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)

        layout.addStretch()

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QFrame#container {
                background: rgba(18, 30, 44, 245);
                border: 1px solid rgba(52, 78, 102, 180);
                border-radius: 16px;
            }

            QPushButton#closeDialogBtn {
                background: transparent;
                color: rgba(207, 233, 255, 0.4);
                border: none;
                border-radius: 14px;
                font: bold 18px 'Segoe UI', 'Inter', sans-serif;
            }

            QPushButton#closeDialogBtn:hover {
                background: rgba(255, 107, 107, 0.2);
                color: #ff6b6b;
            }

            QPushButton#closeDialogBtn:pressed {
                background: rgba(255, 107, 107, 0.3);
                color: #ff4c4c;
            }

            QLabel#title {
                color: #cfe9ff;
                font: bold 22px 'Segoe UI', 'Inter', sans-serif;
                letter-spacing: 0.5px;
            }

            QLabel#subtitle {
                color: rgba(207, 233, 255, 0.6);
                font: 12px 'Segoe UI', 'Inter', sans-serif;
            }

            QLabel#fieldLabel {
                color: rgba(207, 233, 255, 0.8);
                font: 600 11px 'Segoe UI', 'Inter', sans-serif;
                letter-spacing: 0.3px;
            }

            QLineEdit#inputField {
                background: rgba(20, 36, 54, 200);
                border: 1px solid rgba(52, 78, 102, 180);
                border-radius: 10px;
                padding: 10px 14px;
                color: #e5f3ff;
                font: 13px 'Segoe UI', 'Inter', sans-serif;
            }

            QLineEdit#inputField:focus {
                border: 1px solid #057FCA;
            }

            QLineEdit#inputField::placeholder {
                color: rgba(207, 233, 255, 0.4);
            }

            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #057FCA, stop:1 #0598e0);
                border: none;
                border-radius: 10px;
                color: white;
                font: bold 13px 'Segoe UI', 'Inter', sans-serif;
                letter-spacing: 0.5px;
            }

            QPushButton#primaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0690db, stop:1 #06a8f0);
            }

            QPushButton#primaryBtn:pressed {
                background: #046da8;
            }

            QPushButton#secondaryBtn {
                background: transparent;
                border: 1px solid rgba(52, 78, 102, 180);
                border-radius: 10px;
                color: #cfe9ff;
                font: 600 12px 'Segoe UI', 'Inter', sans-serif;
            }

            QPushButton#secondaryBtn:hover {
                background: rgba(52, 78, 102, 80);
                border-color: #057FCA;
            }

            QFrame#dividerLine {
                background: rgba(52, 78, 102, 180);
            }

            QLabel#dividerText {
                color: rgba(207, 233, 255, 0.5);
                font: 11px 'Segoe UI', 'Inter', sans-serif;
                padding: 0 12px;
            }

            QLabel#statusLabel {
                color: #ff6b6b;
                font: 11px 'Segoe UI', 'Inter', sans-serif;
                min-height: 20px;
            }
        """
        )

    def _set_status(self, text: str, ok: bool) -> None:
        self.status_label.setText(text)
        if ok:
            self.status_label.setStyleSheet("color: rgba(207, 233, 255, 0.6);")
        else:
            self.status_label.setStyleSheet("color: #ff6b6b;")
        self.repaint()

    def _on_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self._set_status("Please enter email and password", ok=False)
            return

        self._set_status("Signing in...", ok=True)

        try:
            self.auth_manager.login(email, password)
            self.success = True
            self.accept()
        except RuntimeError as e:
            self._set_status(str(e), ok=False)

    def _on_use_api_key(self):
        msg = self.api_key_input.text().strip()
        if not msg:
            self._set_status("Please enter an API Key", ok=False)
            return

        if not msg.startswith("AIza"):
            self._set_status("Invalid API Key format (should start with AIza)", ok=False)
            return

        self._set_status("Verifying key...", ok=True)
        
        # Save to .env
        try:
            from config import Config
            env_path = os.path.join(Config.PROJECT_ROOT, ".env")
            
            # Read existing
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            
            # Remove existing key if present
            lines = [l for l in lines if not l.startswith("GEMINI_API_KEY=")]
            
            # Add new key
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(f"GEMINI_API_KEY={msg}\n")
            
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                
            # Update runtime config
            os.environ["GEMINI_API_KEY"] = msg
            Config.GEMINI_API_KEY = msg
            Config.USE_DIRECT_API = True
            
            self.success = True
            self.accept()
            
        except Exception as e:
            self._set_status(f"Failed to save key: {e}", ok=False)

    def _on_register(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self._set_status("Please enter email and password", ok=False)
            return

        if len(password) < 6:
            self._set_status("Password must be at least 6 characters", ok=False)
            return

        self._set_status("Creating account...", ok=True)

        try:
            self.auth_manager.register(email, password)
            self.success = True
            self.accept()
        except RuntimeError as e:
            self._set_status(str(e), ok=False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()


def require_login() -> bool:
    """
    Show login dialog if user is not logged in.
    Returns True if user is logged in (or just logged in), False if cancelled.
    """
    auth = get_auth_manager()
    if auth.is_logged_in and auth.verify_token():
        return True

    dialog = LoginDialog()
    dialog.exec()
    return dialog.success
