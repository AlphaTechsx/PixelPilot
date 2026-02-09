"""
Login dialog for user authentication.
Shows email/password fields with login and register options.
Styled to match the PixelPilot UI theme.
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
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor
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
        """Setup the dialog UI."""
        self.setWindowTitle("PixelPilot - Login")
        self.setFixedSize(420, 480)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Main container with styling
        self.container = QFrame(self)
        self.container.setObjectName("container")
        self.container.setGeometry(0, 0, 420, 480)

        layout = QVBoxLayout(self.container)
        layout.setSpacing(12)
        layout.setContentsMargins(32, 28, 32, 28)

        # Logo
        logo_container = QFrame()
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setAlignment(Qt.AlignCenter)

        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "logos",
            "pixelpilot-logo-creative.svg",
        )
        if os.path.exists(logo_path):
            renderer = QSvgRenderer(logo_path)
            if renderer.isValid():
                pixmap = QPixmap(200, 60)
                pixmap.fill(QColor("transparent"))
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                logo_label = QLabel()
                logo_label.setPixmap(pixmap)
                logo_layout.addWidget(logo_label)

        layout.addWidget(logo_container)
        layout.addSpacing(8)

        # Title
        title = QLabel("Welcome Back")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Sign in to continue to PixelPilot")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        # Email field
        email_label = QLabel("Email")
        email_label.setObjectName("fieldLabel")
        layout.addWidget(email_label)

        self.email_input = QLineEdit()
        self.email_input.setObjectName("inputField")
        self.email_input.setPlaceholderText("Enter your email")
        self.email_input.setMinimumHeight(42)
        layout.addWidget(self.email_input)

        layout.addSpacing(8)

        # Password field
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

        # Login button
        self.login_btn = QPushButton("Sign In")
        self.login_btn.setObjectName("primaryBtn")
        self.login_btn.setMinimumHeight(44)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self._on_login)
        layout.addWidget(self.login_btn)

        # Divider
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

        # Register button
        self.register_btn = QPushButton("Create Account")
        self.register_btn.setObjectName("secondaryBtn")
        self.register_btn.setMinimumHeight(44)
        self.register_btn.setCursor(Qt.PointingHandCursor)
        self.register_btn.clicked.connect(self._on_register)
        layout.addWidget(self.register_btn)

        layout.addSpacing(8)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Close button (absolute positioning)
        self.close_btn = QPushButton("×", self.container)
        self.close_btn.setObjectName("closeDialogBtn")
        self.close_btn.setGeometry(380, 12, 28, 28)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)

        layout.addStretch()

    def _apply_styles(self):
        """Apply PixelPilot theme styles."""
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

    def _on_login(self):
        """Handle login button click."""
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.status_label.setText("Please enter email and password")
            return

        self.status_label.setText("Signing in...")
        self.status_label.setStyleSheet("color: rgba(207, 233, 255, 0.6);")
        self.repaint()

        try:
            self.auth_manager.login(email, password)
            self.success = True
            self.accept()
        except RuntimeError as e:
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: #ff6b6b;")

    def _on_register(self):
        """Handle register button click."""
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.status_label.setText("Please enter email and password")
            return

        if len(password) < 6:
            self.status_label.setText("Password must be at least 6 characters")
            return

        self.status_label.setText("Creating account...")
        self.status_label.setStyleSheet("color: rgba(207, 233, 255, 0.6);")
        self.repaint()

        try:
            self.auth_manager.register(email, password)
            self.success = True
            self.accept()
        except RuntimeError as e:
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: #ff6b6b;")

    def mousePressEvent(self, event):
        """Enable dragging the frameless window."""
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        """Handle window dragging."""
        if event.buttons() == Qt.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()


def require_login() -> bool:
    """
    Show login dialog if user is not logged in.

    Returns:
        True if user is logged in (or just logged in), False if cancelled.
    """
    auth = get_auth_manager()

    # Check if already logged in with valid token
    if auth.is_logged_in:
        if auth.verify_token():
            return True

    # Show login dialog
    dialog = LoginDialog()
    result = dialog.exec()

    return dialog.success
