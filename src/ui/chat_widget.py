import os
import re
from uuid import uuid4

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextBrowser,
    QLineEdit,
    QPushButton,
    QLabel,
    QFrame,
    QComboBox,
)
from PySide6.QtCore import Qt, Signal, QUrl, QTimer
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtSvgWidgets import QSvgWidget

from services.audio import AudioService
from .voice_visualizer import VoiceVisualizer
from .sidecar_preview import EmbeddedAgentPreview
from config import OperationMode, Config


class ChatWidget(QWidget):
    command_received = Signal(str)
    mode_changed = Signal(object)
    vision_changed = Signal(str)

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
        self.guidance_btn.clicked.connect(self._on_guidance_btn_clicked)
        self.mode_combo.currentIndexChanged.connect(self.update_mode_tooltip)
        self.mode_combo.currentIndexChanged.connect(self._emit_mode_changed)
        self.vision_combo.currentIndexChanged.connect(self.update_vision_tooltip)
        self.vision_combo.currentIndexChanged.connect(self._emit_vision_changed)
        self.agent_view_btn.clicked.connect(self._toggle_agent_view)

        self.view_mode = "bar_only"
        self._agent_view_enabled = False
        self._agent_view_requested = False

        self._chat_model: list[dict] = []
        self._turn_active = False
        self._thinking_id: str | None = None
        self._stream_timer: QTimer | None = None
        self._stream_target_id: str | None = None
        self._stream_full_text: str = ""
        self._stream_pos: int = 0
        self._guidance_payload: dict | None = None
        self._guidance_active: bool = False
        self._guidance_input_active: bool = False
        self._guidance_input_payload: dict | None = None

        self.set_view_mode("bar_only")
        self.update_mode_tooltip()
        self.update_vision_tooltip()

        self.set_vision_mode("ROBO" if Config.USE_ROBOTICS_EYE else "OCR")
        self.set_workspace_status(Config.DEFAULT_WORKSPACE)

    def set_operation_mode(self, mode: OperationMode):
        """Update dropdown without re-triggering mode change logic."""
        try:
            self.mode_combo.blockSignals(True)
            if mode == OperationMode.GUIDE:
                self.mode_combo.setCurrentIndex(0)
            elif mode == OperationMode.SAFE:
                self.mode_combo.setCurrentIndex(1)
            elif mode == OperationMode.AUTO:
                self.mode_combo.setCurrentIndex(2)
            else:
                self.mode_combo.setCurrentIndex(1)
        finally:
            self.mode_combo.blockSignals(False)
        self.update_mode_tooltip()

    def _emit_mode_changed(self):
        text = self.mode_combo.currentText().strip().upper()
        if text == "GUIDANCE":
            self.mode_changed.emit(OperationMode.GUIDE)
        elif text == "SAFE":
            self.mode_changed.emit(OperationMode.SAFE)
        elif text == "AUTO":
            self.mode_changed.emit(OperationMode.AUTO)
        else:
            self.mode_changed.emit(OperationMode.SAFE)

    def set_vision_mode(self, mode: str):
        """Update vision dropdown without re-triggering signal logic."""
        mode_key = (mode or "").strip().upper()
        try:
            self.vision_combo.blockSignals(True)
            if mode_key == "ROBO":
                self.vision_combo.setCurrentIndex(0)
            elif mode_key == "OCR":
                self.vision_combo.setCurrentIndex(1)
            else:
                self.vision_combo.setCurrentIndex(0)
        finally:
            self.vision_combo.blockSignals(False)
        self.update_vision_tooltip()

    def set_workspace_status(self, workspace: str):
        key = (workspace or "").strip().lower()
        if key not in {"user", "agent"}:
            key = "user"

        label = "USER" if key == "user" else "AGENT"
        self.workspace_badge.setText(label)
        self.workspace_badge.setProperty("workspace", key)
        self.workspace_badge.setToolTip(
            "Current workspace: User Desktop" if key == "user" else "Current workspace: Agent Desktop"
        )

        self.workspace_badge.style().unpolish(self.workspace_badge)
        self.workspace_badge.style().polish(self.workspace_badge)
        self.set_agent_view_enabled(key == "agent")

    def set_expanded(self, expanded: bool):
        self.set_view_mode("extended" if expanded else "bar_only")

    def set_agent_view_enabled(self, enabled: bool):
        self._agent_view_enabled = bool(enabled)
        self.agent_view_btn.setEnabled(self._agent_view_enabled)
        if self._agent_view_enabled:
            self.agent_view_btn.setToolTip("Toggle live Agent Desktop view")
        else:
            self.agent_view_btn.setToolTip("Agent view is available only on agent workspace")
            self._agent_view_requested = False
        self._apply_view_mode()

    def set_agent_preview_source(self, desktop_manager_or_none):
        self.agent_preview.set_capture_source(desktop_manager_or_none)
        if desktop_manager_or_none is None:
            self._agent_view_requested = False
        self._apply_view_mode()

    def _toggle_agent_view(self):
        if not self._agent_view_enabled:
            return
        self._agent_view_requested = not self._agent_view_requested
        self._apply_view_mode()

    def _emit_vision_changed(self):
        text = self.vision_combo.currentText().strip().upper()
        if text == "ROBO":
            self.vision_changed.emit("robo")
        elif text == "OCR":
            self.vision_changed.emit("ocr")
        else:
            self.vision_changed.emit("robo")

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.extended_panel = QFrame()
        self.extended_panel.setObjectName("extendedPanel")
        ep = QVBoxLayout(self.extended_panel)
        ep.setContentsMargins(10, 10, 10, 10)
        ep.setSpacing(8)

        self.settings_row = QFrame()
        self.settings_row.setObjectName("settingsRow")
        sr = QHBoxLayout(self.settings_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(8)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["GUIDANCE", "SAFE", "AUTO"])
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.setItemData(0, "Step-by-step guidance with continuous human input.", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(1, "Balanced autonomy. Confirms potentially dangerous actions.", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(2, "Minimal interaction. PIXIE runs tasks end-to-end.", Qt.ItemDataRole.ToolTipRole)

        self.vision_combo = QComboBox()
        self.vision_combo.addItems(["ROBO", "OCR"])
        self.vision_combo.setObjectName("visionCombo")
        self.vision_combo.setItemData(0, "Robotics vision (Gemini Robotics-ER).", Qt.ItemDataRole.ToolTipRole)
        self.vision_combo.setItemData(1, "Local OCR + CV (EasyOCR + OpenCV).", Qt.ItemDataRole.ToolTipRole)

        self.workspace_badge = QLabel("USER")
        self.workspace_badge.setObjectName("workspaceBadge")
        self.workspace_badge.setToolTip("Current workspace")

        self.logout_btn = QPushButton("Sign Out")
        self.logout_btn.setObjectName("logoutLink")
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.setToolTip("Sign out and switch accounts")

        sr.addWidget(self.mode_combo)
        sr.addWidget(self.vision_combo)
        sr.addWidget(self.workspace_badge)
        sr.addStretch()
        sr.addWidget(self.logout_btn)
        ep.addWidget(self.settings_row)

        self.process_panel = QFrame()
        self.process_panel.setObjectName("processPanel")
        pp = QVBoxLayout(self.process_panel)
        pp.setContentsMargins(0, 0, 0, 0)
        pp.setSpacing(6)

        self.process_title = QLabel("Process Details")
        self.process_title.setObjectName("sectionTitle")
        pp.addWidget(self.process_title)

        self.chat_display = QTextBrowser()
        self.chat_display.setObjectName("chatDisplay")
        self.chat_display.setReadOnly(True)
        self.chat_display.setAcceptRichText(True)
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self._on_anchor_clicked)
        self.chat_display.setPlaceholderText("Ask anything to Pixie.")
        pp.addWidget(self.chat_display)

        self.voice_visualizer = VoiceVisualizer()
        self.voice_visualizer.setObjectName("voiceVisualizer")
        self.voice_visualizer.setVisible(False)
        pp.addWidget(self.voice_visualizer)

        self.compact_stop_btn = QPushButton("Stop")
        self.compact_stop_btn.setObjectName("compactStopBtn")
        self.compact_stop_btn.setFixedHeight(28)
        self.compact_stop_btn.setVisible(False)
        self.compact_stop_btn.setToolTip("Stop voice session")
        self.compact_stop_btn.clicked.connect(self.audio_service.stop_listening)
        pp.addWidget(self.compact_stop_btn)

        self.input_hint = QLabel("Open apps, send emails/WhatsApp, fix PC issues, or ask anything...")
        self.input_hint.setObjectName("inputHint")
        self.input_hint.setWordWrap(True)
        pp.addWidget(self.input_hint)

        self.guidance_bar = QFrame()
        self.guidance_bar.setObjectName("guidanceBar")
        g = QVBoxLayout(self.guidance_bar)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(6)

        self.guidance_btn = QPushButton("Next Step")
        self.guidance_btn.setObjectName("guidanceBtn")
        self.guidance_btn.setFixedSize(110, 32)
        self.guidance_btn.setVisible(False)

        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setObjectName("continueBtn")
        self.continue_btn.setFixedSize(110, 32)
        self.continue_btn.setVisible(False)
        self.continue_btn.clicked.connect(self._on_continue_btn_clicked)

        button_row = QWidget()
        b = QHBoxLayout(button_row)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(6)
        b.addStretch()
        b.addWidget(self.continue_btn)
        b.addWidget(self.guidance_btn)
        g.addWidget(button_row)
        self.guidance_bar.setVisible(False)
        pp.addWidget(self.guidance_bar)

        ep.addWidget(self.process_panel)

        self.agent_separator = QFrame()
        self.agent_separator.setObjectName("agentSeparator")
        self.agent_separator.setFrameShape(QFrame.Shape.HLine)
        self.agent_separator.setFrameShadow(QFrame.Shadow.Plain)

        self.agent_panel = QFrame()
        self.agent_panel.setObjectName("agentPanel")
        ap = QVBoxLayout(self.agent_panel)
        ap.setContentsMargins(0, 0, 0, 0)
        ap.setSpacing(6)

        self.agent_panel_title = QLabel("Agent Desktop View")
        self.agent_panel_title.setObjectName("sectionTitle")
        ap.addWidget(self.agent_panel_title)

        self.agent_preview = EmbeddedAgentPreview(self, fps=Config.SIDECAR_PREVIEW_FPS)
        ap.addWidget(self.agent_preview)

        self.extended_panel.setVisible(False)

        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        h = QHBoxLayout(self.top_bar)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(10)

        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logos", "pixelpilot-icon.svg"))
        self.logo = QSvgWidget(logo_path)
        self.logo.setObjectName("logo")
        self.logo.setFixedSize(32, 32)

        self.input_shell = QFrame()
        self.input_shell.setObjectName("barInput")
        i = QHBoxLayout(self.input_shell)
        i.setContentsMargins(10, 4, 10, 4)
        i.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("Ask Pixel Pilot to do anything...")

        self.mic_btn = QPushButton("Mic")
        self.mic_btn.setObjectName("micBtn")
        self.mic_btn.setFixedSize(40, 30)
        self.mic_btn.setToolTip("Start listening")

        self.send_btn = QPushButton("Go")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(38, 30)

        i.addWidget(self.input_field, 1)
        i.addWidget(self.mic_btn)
        i.addWidget(self.send_btn)

        self.expand_btn = QPushButton("Details")
        self.expand_btn.setObjectName("expandBtn")
        self.expand_btn.setFixedHeight(32)
        self.expand_btn.setToolTip("Show process details")

        self.agent_view_btn = QPushButton("Agent View")
        self.agent_view_btn.setObjectName("agentViewBtn")
        self.agent_view_btn.setFixedHeight(32)
        self.agent_view_btn.setToolTip("Agent view is available only on agent workspace")
        self.agent_view_btn.setEnabled(False)

        self.minimize_btn = QPushButton("Min")
        self.minimize_btn.setObjectName("minimizeBtn")
        self.minimize_btn.setFixedHeight(32)
        self.minimize_btn.setToolTip("Hide to background")

        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedHeight(32)

        h.addWidget(self.logo)
        h.addWidget(self.input_shell, 1)
        h.addWidget(self.expand_btn)
        h.addWidget(self.agent_view_btn)
        h.addWidget(self.minimize_btn)
        h.addWidget(self.close_btn)

        self.agent_below_container = QFrame()
        self.agent_below_container.setObjectName("agentBelowContainer")
        abc = QVBoxLayout(self.agent_below_container)
        abc.setContentsMargins(0, 0, 0, 0)
        abc.setSpacing(6)
        abc.addWidget(self.agent_separator)
        abc.addWidget(self.agent_panel)
        self.agent_below_container.setVisible(False)

        layout.addWidget(self.extended_panel)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.agent_below_container)
        self.setLayout(layout)

    def apply_styles(self):
        self.setStyleSheet("""
            QToolTip { background: #111827; color: #f9fafb; border: 1px solid #1f2937; padding: 6px 10px; font: 11px 'Segoe UI', sans-serif; }
            ChatWidget { background: transparent; font-family: 'Segoe UI', sans-serif; }
            QFrame#topBar {
                background: #f8fafc;
                border: 1px solid #d3dce8;
                border-radius: 22px;
            }
            QFrame#barInput {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 14px;
            }
            QLineEdit#inputField {
                background: transparent;
                border: none;
                color: #1f2937;
                font: 15px 'Segoe UI', sans-serif;
            }
            QPushButton#sendBtn, QPushButton#micBtn {
                background: #eef2f7;
                border: 1px solid #d5dce7;
                border-radius: 10px;
                color: #334155;
                font: 600 11px 'Segoe UI', sans-serif;
            }
            QPushButton#sendBtn:hover, QPushButton#micBtn:hover { background: #e2e8f0; border-color: #bcc7d6; }
            QPushButton#expandBtn, QPushButton#agentViewBtn, QPushButton#minimizeBtn, QPushButton#closeBtn {
                background: #ffffff;
                border: 1px solid #d2dae5;
                border-radius: 10px;
                color: #273345;
                font: 600 12px 'Segoe UI', sans-serif;
                padding: 0 10px;
            }
            QPushButton#expandBtn:hover, QPushButton#agentViewBtn:hover, QPushButton#minimizeBtn:hover {
                background: #f1f5f9;
                border-color: #bfcbdc;
            }
            QPushButton#agentViewBtn:disabled {
                color: #9ca3af;
                background: #f4f6f8;
                border-color: #e5e7eb;
            }
            QPushButton#closeBtn:hover {
                background: #fee2e2;
                color: #7f1d1d;
                border-color: #fecaca;
            }
            QFrame#extendedPanel {
                background: #f8fafc;
                border: 1px solid #d3dce8;
                border-radius: 16px;
            }
            QFrame#settingsRow {
                background: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 10px;
                padding: 6px;
            }
            QComboBox#modeCombo, QComboBox#visionCombo {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 5px 10px;
                font: 600 12px 'Segoe UI', sans-serif;
                min-width: 88px;
            }
            QComboBox#visionCombo { min-width: 64px; }
            QComboBox#modeCombo::drop-down, QComboBox#visionCombo::drop-down { border: none; width: 14px; }
            QComboBox#modeCombo::down-arrow, QComboBox#visionCombo::down-arrow { image: none; }
            QLabel#workspaceBadge {
                background: #f3f4f6;
                color: #334155;
                border: 1px solid #d5dce7;
                border-radius: 8px;
                padding: 2px 8px;
                font: 700 10px 'Segoe UI', sans-serif;
                min-width: 52px;
                qproperty-alignment: 'AlignCenter';
            }
            QLabel#workspaceBadge[workspace="user"] {
                background: #fff7ed;
                color: #9a3412;
                border: 1px solid #fed7aa;
            }
            QLabel#workspaceBadge[workspace="agent"] {
                background: #ecfdf3;
                color: #166534;
                border: 1px solid #bbf7d0;
            }
            QPushButton#logoutLink {
                background: transparent;
                border: none;
                color: #d14343;
                font: 700 12px 'Segoe UI', sans-serif;
                padding: 4px 8px;
            }
            QPushButton#logoutLink:hover { text-decoration: underline; }
            QLabel#sectionTitle {
                color: #334155;
                font: 700 12px 'Segoe UI', sans-serif;
                padding-left: 2px;
            }
            QFrame#agentSeparator {
                color: #d5deea;
                background: #d5deea;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }
            QTextEdit#chatDisplay {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d9e2ec;
                border-radius: 10px;
                font: 14px 'Segoe UI', sans-serif;
                padding: 10px;
            }
            QLabel#inputHint { color: #64748b; font: 12px 'Segoe UI', sans-serif; padding: 2px 2px; }
            QWidget#voiceVisualizer { background: #eef2f7; border: 1px solid #d5deea; border-radius: 10px; }
            QPushButton#compactStopBtn {
                background: #fff7ed;
                color: #9a3412;
                border: 1px solid #fed7aa;
                border-radius: 8px;
                font: 700 11px 'Segoe UI', sans-serif;
                padding: 5px 10px;
            }
            QPushButton#guidanceBtn {
                background: #2563eb;
                color: #ffffff;
                border: 1px solid #1d4ed8;
                border-radius: 8px;
                font: 700 13px 'Segoe UI', sans-serif;
                padding: 4px 12px;
            }
            QPushButton#guidanceBtn:hover { background: #1d4ed8; }
            QPushButton#continueBtn {
                background: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font: 600 13px 'Segoe UI', sans-serif;
                padding: 4px 12px;
            }
            QPushButton#continueBtn:hover { background: #f1f5f9; border-color: #94a3b8; }
        """)
    def _on_anchor_clicked(self, url: QUrl):
        u = url.toString()
        if u.startswith("pp://thinking/toggle/"):
            thinking_id = u.split("pp://thinking/toggle/", 1)[-1]
            for msg in self._chat_model:
                if msg.get("kind") == "thinking" and msg.get("id") == thinking_id:
                    msg["expanded"] = not bool(msg.get("expanded"))
                    break
            self._render_chat()
            return

    def _start_turn(self):
        self._turn_active = True
        self._thinking_id = None

    def _is_activity_line(self, text: str) -> bool:
        s = (text or "").strip()
        if not s:
            return False
        low = s.lower()
        markers = (
            "executing:",
            "new task",
            "--- step",
            "planning action",
            "ai agent initialized",
            "startup",
            "logging to:",
            "admin:",
            "taking screenshot",
            "verifying task completion",
            "scanning ui elements",
        )
        return low.startswith(markers) or "planning action" in low

    def _hide_or_route_nonfinal_gui_line(self, s: str) -> str:
        """Returns: 'hide' | 'activity' | 'show'.

        Goal: main chat shows only user + final assistant answer (+ errors).
        Everything else is either hidden (internal implementation details) or routed to Thinking.
        """

        low = (s or "").strip().lower()
        if not low:
            return "hide"

        if low in {"stopped"}:
            return "show"

        hide_markers = (
            "blind mode",
            "[blind]",
            "[vision]",
            "[mode switch]",
            "activating vision system",
            "switching to blind mode",
            "switching to vision",
            "vision requested",
            "gemini robotics-er",
            "bounding box",
            "ocr + edge detection",
            "found ",
            "ui elements",
            "vision set to",
        )
        if any(m in low for m in hide_markers):
            return "hide"

        # Route automation diagnostics/progress into Thinking.
        activity_markers = (
            "taking screenshot",
            "screenshot attempt",
            "uac detected",
            "orchestrator",
            "waiting ",
            "key combination:",
            "pressing key:",
            "typing:",
            "clicking ",
            "opening application:",
            "browser skill result",
            "media skill result",
            "system skill result",
            "timer skill result",
            "action completed",
            "task completed",
            "task verified",
            "verifying task completion",
            "planning next action",
            "planning action",
            "executing sequence",
            "sequence step",
            "next action:",
        )
        if any(m in low for m in activity_markers) or self._is_activity_line(s):
            return "activity"

        return "activity"

    def _activity_title_from_line(self, line: str) -> str:
        s = (line or "").strip()
        low = s.lower()
        if low.startswith("executing:"):
            return "Executing"
        if low.startswith("new task"):
            return "New task"
        if "planning action" in low:
            return "Planning"
        if low.startswith("--- step") or low.startswith("step "):
            return s.replace("---", "").strip()
        if low.startswith("startup") or low.startswith("admin:") or low.startswith("logging to:"):
            return "Startup"
        return "Thinking"

    def _thinking_title_from_state(self, state: dict) -> str:
        if state.get("done"):
            return "Thinking (done)"

        seq_total = state.get("seq_total")
        seq_step = state.get("seq_step")
        if isinstance(seq_total, int) and seq_total > 0 and isinstance(seq_step, int) and seq_step > 0:
            return f"Thinking ({seq_step}/{seq_total})"

        action = (state.get("action") or "").strip()
        if action:
            return f"Thinking ({action})"

        step_total = state.get("step_total")
        step = state.get("step")
        if isinstance(step_total, int) and step_total > 0 and isinstance(step, int) and step > 0:
            return f"Thinking (step {step}/{step_total})"
        phase = (state.get("phase") or "").strip()
        if phase:
            return f"Thinking ({phase.lower()})"
        return "Thinking"

    def _format_activity_for_thinking(self, raw_line: str, state: dict) -> tuple[bool, str | None]:
        """Turn noisy backend trace lines into clean, user-facing thinking updates.

        Returns (reset_lines, append_line). append_line=None means 'do not show a line'.
        """

        line = (raw_line or "").strip()
        low = line.lower()

        # NEW TASK: <text>
        m = re.match(r"^new\s+task\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            task = (m.group(1) or "").strip()
            state.clear()
            if task:
                state["task"] = task
            state["phase"] = "starting"
            state["done"] = False
            return True, "Task received"

        # --- Step X/Y ---
        m = re.match(r"^---\s*step\s*(\d+)\s*/\s*(\d+)\s*---$", low)
        if m:
            try:
                state["step"] = int(m.group(1))
                state["step_total"] = int(m.group(2))
            except Exception:
                pass
            state.setdefault("phase", "working")
            return False, None

        # --- Sequence Step X/Y --- (more human-friendly than raw actions)
        m = re.match(r"^---\s*sequence\s*step\s*(\d+)\s*/\s*(\d+)\s*---$", low)
        if m:
            try:
                state["seq_step"] = int(m.group(1))
                state["seq_total"] = int(m.group(2))
            except Exception:
                pass
            state.setdefault("phase", "working")
            return False, None

        if "planning action" in low:
            state["phase"] = "planning"
            state["action"] = "planning"
            return False, "Planning next step..."

        # Executing: <something>
        m = re.match(r"^executing\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            state["phase"] = "working"
            state["action"] = "working"
            detail = (m.group(1) or "").strip()
            if detail:
                return False, f"Working on: {detail}"
            return False, "Working..."

        # Action: reply / click / type / etc.
        m = re.match(r"^action\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            action = (m.group(1) or "").strip()
            state["phase"] = "working"
            action_low = action.lower()
            action_map = {
                "reply": ("replying", "Composing response..."),
                "type_text": ("typing", "Typing..."),
                "key_combo": ("shortcut", "Pressing a shortcut..."),
                "click": ("clicking", "Clicking..."),
                "wait": ("waiting", "Waiting..."),
                "scroll": ("scrolling", "Scrolling..."),
                "open_app": ("opening", "Opening an app..."),
            }
            if action_low in action_map:
                state["action"], friendly = action_map[action_low]
                # Only show when it changes; repeated actions are noisy.
                if state.get("_last_action_shown") != state["action"]:
                    state["_last_action_shown"] = state["action"]
                    return False, friendly
                return False, None
            if action:
                state["action"] = action_low
                if state.get("_last_action_shown") != action_low:
                    state["_last_action_shown"] = action_low
                    return False, "Working..."
            return False, None

        # Next action: <...> (some backends log this form)
        m = re.match(r"^next\s+action\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            a = (m.group(1) or "").strip().lower()
            # Reuse the same mapping as Action:
            return self._format_activity_for_thinking(f"Action: {a}", state)

        # Reasoning is internal and tends to be spammy.
        if low.startswith("reasoning:"):
            return False, None

        m = re.match(r"^task\s+completed\s*:?\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            state["phase"] = "done"
            state["done"] = True
            state["action"] = "done"
            return False, "Completed"

        if low == "done":
            state["phase"] = "done"
            state["action"] = "done"
            return False, "Finished"

        # Screenshot/UAC noise -> summarize.
        if "taking screenshot" in low:
            state["phase"] = "working"
            state["action"] = "checking"
            if state.get("_last_action_shown") != "checking":
                state["_last_action_shown"] = "checking"
                return False, "Checking the screen..."
            return False, None

        if "uac detected" in low or "orchestrator protocol" in low:
            state["phase"] = "working"
            state["action"] = "permissions"
            if state.get("_last_action_shown") != "permissions":
                state["_last_action_shown"] = "permissions"
                return False, "Handling a permissions prompt..."
            return False, None

        if "screenshot attempt" in low and "failed" in low:
            state["phase"] = "working"
            state["action"] = "retrying"
            if state.get("_last_action_shown") != "retrying":
                state["_last_action_shown"] = "retrying"
                return False, "Retrying..."
            return False, None

        if "error parsing gemini response" in low:
            state["phase"] = "working"
            state["action"] = "recovering"
            if state.get("_last_action_shown") != "recovering":
                state["_last_action_shown"] = "recovering"
                return False, "Recovering from a response error..."
            return False, None

        if "verification screenshot failed" in low:
            state["phase"] = "working"
            state["action"] = "verifying"
            if state.get("_last_action_shown") != "verifying":
                state["_last_action_shown"] = "verifying"
                return False, "Couldn't verify visually; continuing..."
            return False, None

        if "trusting task completion" in low:
            return False, None

        # Default: only keep short, non-noisy lines.
        if len(line) > 160:
            return False, None

        return False, line

    def add_activity_message(self, message: str):
        line = "" if message is None else str(message).rstrip()
        if not line.strip():
            return

        low = line.lower()

        # Never expose internal mode/vision implementation details in the GUI.
        hidden = (
            "blind mode",
            "[blind]",
            "[mode switch]",
            "activating vision system",
            "switching to vision",
            "vision requested",
        )
        if any(s in low for s in hidden):
            cleaned = line.replace("(Blind Mode)", "").replace("Blind Mode", "").strip()
            cleaned_low = cleaned.lower()
            if cleaned and not any(s in cleaned_low for s in hidden):
                line = cleaned
            else:
                return

        if not self._turn_active:
            return

        if self._thinking_id is None:
            self._thinking_id = str(uuid4())
            self._chat_model.append(
                {
                    "kind": "thinking",
                    "id": self._thinking_id,
                    "title": "Thinking",
                    "expanded": False,
                    "lines": [],
                    "state": {},
                }
            )

        for msg in self._chat_model:
            if msg.get("kind") == "thinking" and msg.get("id") == self._thinking_id:
                state = msg.get("state")
                if not isinstance(state, dict):
                    state = {}
                    msg["state"] = state

                reset_lines, append_line = self._format_activity_for_thinking(line, state)
                if reset_lines:
                    msg["lines"] = []

                # Title comes from summarized state, not raw trace.
                msg["title"] = self._thinking_title_from_state(state)

                if append_line:
                    last = msg.get("_last_line")
                    if append_line != last:
                        msg["lines"].append(append_line)
                        msg["_last_line"] = append_line
                        # Keep the thinking panel tight.
                        if len(msg["lines"]) > 40:
                            msg["lines"] = msg["lines"][-40:]
                break

        self._render_chat()

    def update_vision_tooltip(self):
        tip = self.vision_combo.itemData(self.vision_combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        if tip:
            self.vision_combo.setToolTip(tip)

    def send_message(self):
        text = self.input_field.text().strip()
        if text:
            self._hide_input_hint()
            self._start_turn()
            self.add_user_message(text)
            self.input_field.clear()
            # Reset placeholder if it was changed for guidance
            if self._guidance_input_active or self._guidance_active:
                self.input_field.setPlaceholderText("Ask Pixel Pilot to do anything...")
            # Check for new conversational guidance input first
            if self._send_guidance_input(text):
                return
            # Check for legacy guidance feedback
            if self._send_guidance_feedback(text):
                return
            self.send_to_agent(text)

    def _append_message(self, *, kind: str, text: str):
        # Backwards-compat: this method now appends to an internal model then re-renders.
        self._append_to_model(kind=kind, text=text)

    def _insert_bubble(
        self,
        cursor: QTextCursor,
        *,
        kind: str,
        text: str,
        actions: list[dict] | None = None,
        completed: bool = False,
    ):
        kind_key = (kind or "").lower().strip()

        viewport_width = max(300, self.chat_display.viewport().width())
        max_width = int(viewport_width * 0.7)
        side_margin = max(10, viewport_width - max_width)

        # Defaults
        alignment = Qt.AlignmentFlag.AlignLeft
        bubble_bg = QColor("#0c1f2f")
        text_color = QColor("#e6f3ff")
        font_weight = -1
        italic = False

        if kind_key == "user":
            alignment = Qt.AlignmentFlag.AlignRight
            bubble_bg = QColor("#0f2f43")
        elif kind_key == "assistant":
            alignment = Qt.AlignmentFlag.AlignLeft
            bubble_bg = QColor("#0b1b27")
            text_color = QColor("#e6f3ff")
        elif kind_key == "system":
            alignment = Qt.AlignmentFlag.AlignLeft
            bubble_bg = QColor("#0b1b27")
            text_color = QColor("#cfe9ff")
            italic = True
        elif kind_key == "output":
            alignment = Qt.AlignmentFlag.AlignLeft
            bubble_bg = QColor("#071724")
            text_color = QColor("#cfe9ff")
        elif kind_key == "error":
            alignment = Qt.AlignmentFlag.AlignLeft
            bubble_bg = QColor("#2a0f17")
            text_color = QColor("#ffd7e0")
            font_weight = 75  # QFont.Bold
        elif kind_key == "section":
            alignment = Qt.AlignmentFlag.AlignCenter
            bubble_bg = QColor("#00000000")
            text_color = QColor("#9fd0ff")
            font_weight = 75
            italic = True

        block_format = QTextBlockFormat()
        block_format.setAlignment(alignment)

        # Only apply side margins for left/right aligned "bubble" messages
        if alignment == Qt.AlignmentFlag.AlignRight:
            block_format.setLeftMargin(side_margin)
            block_format.setRightMargin(0)
        elif alignment == Qt.AlignmentFlag.AlignLeft:
            block_format.setLeftMargin(0)
            block_format.setRightMargin(side_margin)

        char_format = QTextCharFormat()
        char_format.setForeground(text_color)
        char_format.setBackground(bubble_bg)
        if font_weight > 0:
            char_format.setFontWeight(font_weight)
        if italic:
            char_format.setFontItalic(True)

        if completed and kind_key == "assistant":
            text_color = QColor("#9fb9cc")
            bubble_bg = QColor("#091521")
            char_format.setForeground(text_color)
            char_format.setBackground(bubble_bg)
            display_text = f"[OK] {text}"
        else:
            display_text = text

        cursor.insertBlock(block_format)
        cursor.insertText(display_text, char_format)

        cursor.insertBlock()

    def _render_thinking(self, cursor: QTextCursor, msg: dict):
        title = msg.get("title") or "Thinking"
        expanded = bool(msg.get("expanded"))
        thinking_id = msg.get("id")
        lines: list[str] = msg.get("lines") or []
        state: dict = msg.get("state") if isinstance(msg.get("state"), dict) else {}

        block = QTextBlockFormat()
        block.setAlignment(Qt.AlignmentFlag.AlignLeft)
        block.setLeftMargin(0)
        block.setRightMargin(0)
        cursor.insertBlock(block)

        link = QTextCharFormat()
        link.setForeground(QColor("#9fd0ff"))
        link.setFontItalic(True)
        link.setAnchor(True)
        link.setAnchorHref(f"pp://thinking/toggle/{thinking_id}")
        link.setUnderlineStyle(QTextCharFormat.UnderlineStyle.NoUnderline)
        chevron = "v" if expanded else ">"
        cursor.insertText(f"{title} {chevron}", link)
        cursor.insertBlock()

        if expanded and (lines or state):
            body_block = QTextBlockFormat()
            body_block.setAlignment(Qt.AlignmentFlag.AlignLeft)
            body_block.setLeftMargin(0)
            body_block.setRightMargin(0)

            body_char = QTextCharFormat()
            body_char.setForeground(QColor("#bfe6ff"))
            body_char.setBackground(QColor("#071724"))
            body_char.setFontFamily("Consolas")
            cursor.insertBlock(body_block)

            body_lines: list[str] = []
            task = (state.get("task") or "").strip()
            if task:
                body_lines.append(f"Task: {task}")

            step_total = state.get("step_total")
            step = state.get("step")
            if isinstance(step_total, int) and step_total > 0 and isinstance(step, int) and step > 0:
                body_lines.append(f"Progress: {step}/{step_total}")

            phase = (state.get("phase") or "").strip()
            if phase:
                body_lines.append(f"Status: {phase.capitalize()}")

            if body_lines and lines:
                body_lines.append("")

            # Show only a small, friendly recent trail.
            for l in lines[-12:]:
                body_lines.append(f"- {l}")

            cursor.insertText("\n".join(body_lines).strip(), body_char)
            cursor.insertBlock()

    def _append_to_model(self, *, kind: str, text: str):
        self._chat_model.append({"kind": kind, "text": text})
        self._render_chat()

    def _render_chat(self):
        self.chat_display.setUpdatesEnabled(False)
        try:
            self.chat_display.clear()
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            for msg in self._chat_model:
                kind = (msg.get("kind") or "").lower().strip()
                if kind == "thinking":
                    self._render_thinking(cursor, msg)
                    continue
                text = msg.get("text")
                if text is None:
                    continue
                self._insert_bubble(
                    cursor,
                    kind=kind,
                    text=str(text),
                    actions=msg.get("actions"),
                    completed=bool(msg.get("completed")),
                )
            self.chat_display.setTextCursor(cursor)
            self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())
        finally:
            self.chat_display.setUpdatesEnabled(True)

    def display_message(self, sender, text):
        sender_key = (sender or "").lower().strip()
        if sender_key == "you":
            self._append_message(kind="user", text=text)
        else:
            self._append_message(kind="system", text=text)

    def toggle_listening(self):
        if self.audio_service.is_listening:
            self.audio_service.stop_listening()
        else:
            self.audio_service.start_listening()

    def on_listening_status(self, listening):
        if listening:
            self.mic_btn.setText("Stop")
            self.mic_btn.setToolTip("Stop listening")
            self.input_field.setPlaceholderText("Listening...")
        else:
            self.mic_btn.setText("Mic")
            self.mic_btn.setToolTip("Start listening")
            self.input_field.setPlaceholderText("Ask Pixel Pilot to do anything...")
        self._apply_view_mode()

    def on_speech_text(self, text):
        if not text:
            return
        self._hide_input_hint()
        self._start_turn()
        self.add_user_message(text)
        # Check for new conversational guidance input first
        if self._send_guidance_input(text):
            return
        # Check for legacy guidance feedback
        if self._send_guidance_feedback(text):
            return
        self.send_to_agent(text)

    def _send_guidance_input(self, text: str) -> bool:
        """Handle input for conversational guidance mode."""
        if not self._guidance_input_active:
            return False
        if not isinstance(self._guidance_input_payload, dict):
            return False

        payload = self._guidance_input_payload
        self._guidance_input_payload = None
        self._guidance_input_active = False

        payload["feedback"] = text
        event = payload.get("event")
        if event is not None:
            event.set()

        return True

    def _send_guidance_feedback(self, text: str) -> bool:
        """Handle legacy guidance button feedback."""
        if not self._guidance_active:
            return False
        if not isinstance(self._guidance_payload, dict):
            return False

        payload = self._guidance_payload
        self._guidance_payload = None
        self._guidance_active = False
        self._update_guidance_steps(None)
        self.guidance_bar.hide()
        self.guidance_btn.hide()

        payload["result"] = False
        payload["feedback"] = text
        event = payload.get("event")
        if event is not None:
            event.set()

        self._apply_view_mode()
        return True

    def on_audio_level(self, level):
        self.voice_visualizer.set_level(level)

    def send_to_agent(self, text):
        self.command_received.emit(text)

    def add_system_message(self, message):
        text = "" if message is None else str(message)
        s = text.strip()
        if not s:
            return

        decision = self._hide_or_route_nonfinal_gui_line(s)
        if decision == "hide":
            return
        if decision == "activity":
            self.add_activity_message(s)
            return

        self._append_to_model(kind="system", text=s)

    def add_final_answer(self, message: str):
        text = "" if message is None else str(message)
        s = text.strip("\n")
        if not s:
            return

        # Cancel any prior stream
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()

        msg_id = str(uuid4())
        self._stream_target_id = msg_id
        self._stream_full_text = s
        self._stream_pos = 0

        # Create placeholder assistant bubble
        self._chat_model.append({"kind": "assistant", "id": msg_id, "text": ""})
        self._render_chat()

        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setInterval(30)
            self._stream_timer.timeout.connect(self._tick_stream_answer)

        self._stream_timer.start()

    def _tick_stream_answer(self):
        if not self._stream_target_id:
            if self._stream_timer is not None:
                self._stream_timer.stop()
            return

        # Stream in chunks for performance
        chunk_size = 12
        if self._stream_pos >= len(self._stream_full_text):
            if self._stream_timer is not None:
                self._stream_timer.stop()
            return

        next_pos = min(len(self._stream_full_text), self._stream_pos + chunk_size)
        new_text = self._stream_full_text[:next_pos]
        self._stream_pos = next_pos

        for msg in self._chat_model:
            if msg.get("id") == self._stream_target_id:
                msg["text"] = new_text
                break

        self._render_chat()

    def add_output_message(self, message):
        text = "" if message is None else str(message)
        s = text.strip()
        if not s:
            return

        decision = self._hide_or_route_nonfinal_gui_line(s)
        if decision == "hide":
            return
        if decision == "activity":
            self.add_activity_message(s)
            return

        self._append_to_model(kind="output", text=s)

    def add_user_message(self, message):
        self._append_to_model(kind="user", text=str(message))

    def add_section_header(self, title: str):
        title = (title or "").strip()
        if not title:
            return
        self._append_to_model(kind="section", text=f"--- {title} ---")

    def add_error_message(self, message):
        self._append_to_model(kind="error", text=str(message))

    def update_mode_tooltip(self):
        tip = self.mode_combo.itemData(self.mode_combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        if tip:
            self.mode_combo.setToolTip(tip)

    def set_view_mode(self, mode):
        mode_key = (mode or "").strip().lower()
        if mode_key not in {"bar_only", "extended"}:
            mode_key = "bar_only"
        self.view_mode = mode_key
        if self.view_mode == "bar_only" and self.audio_service.is_listening:
            self.audio_service.stop_listening()
        self._apply_view_mode()

    def _apply_view_mode(self):
        listening = self.audio_service.is_listening
        expanded = self.view_mode == "extended"

        self.extended_panel.setVisible(expanded)
        self.expand_btn.setText("Hide" if expanded else "Details")

        show_agent = bool(expanded and self._agent_view_enabled and self._agent_view_requested)
        self.agent_below_container.setVisible(show_agent)
        self.agent_preview.set_active(show_agent)
        if self._agent_view_enabled:
            self.agent_view_btn.setText("Hide Agent" if show_agent else "Agent View")
        else:
            self.agent_view_btn.setText("Agent View")

        if not expanded:
            self.voice_visualizer.hide()
            self.voice_visualizer.set_active(False)
            self.compact_stop_btn.hide()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            return

        if listening:
            self.chat_display.hide()
            self.input_hint.hide()
            self.voice_visualizer.show()
            self.voice_visualizer.set_active(True)
            self.compact_stop_btn.show()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            return

        self.chat_display.show()
        if self.chat_display.toPlainText().strip():
            self.input_hint.hide()
        else:
            self.input_hint.show()
        self.voice_visualizer.hide()
        self.voice_visualizer.set_active(False)
        self.compact_stop_btn.hide()

        if self._guidance_active or self._guidance_input_active:
            self.guidance_bar.show()
            self.guidance_btn.show()
        else:
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()

    def _hide_input_hint(self):
        if self.input_hint.isVisible():
            self.input_hint.hide()

    def _mark_last_guidance_step_completed(self):
        for msg in reversed(self._chat_model):
            if (msg.get("kind") or "").lower().strip() != "assistant":
                continue
            if msg.get("completed"):
                return
            msg["completed"] = True
            self._render_chat()
            return

    def show_guidance_button(self, label: str, payload: dict):
        text = (label or "Next").strip() or "Next"
        self._guidance_payload = payload
        self._guidance_active = True
        self.guidance_btn.setText(text)
        self.guidance_btn.setEnabled(True)
        self.guidance_bar.show()
        self.guidance_btn.show()
        self._apply_view_mode()

    def hide_guidance_button(self):
        self._guidance_payload = None
        self._guidance_active = False
        self.guidance_bar.hide()
        self.guidance_btn.hide()
        self.continue_btn.hide()
        self._apply_view_mode()

    def show_guidance_input(self, payload: dict):
        """Enable conversational guidance input mode.
        
        The next message from the user will be sent to the guidance session
        instead of starting a new agent task. Also shows a "Next" button.
        """
        self._guidance_input_payload = payload
        self._guidance_input_active = True
        label = (payload.get("label") or "Next").strip() or "Next"
        if payload.get("final"):
            self.input_field.setPlaceholderText("Click Done to finish or type a reply...")
        else:
            self.input_field.setPlaceholderText("Type 'done', ask a question, or describe what happened...")
        self.guidance_btn.setText(label)
        self.guidance_btn.setEnabled(True)
        
        if payload.get("show_continue"):
            self.continue_btn.show()
        else:
            self.continue_btn.hide()
            
        self.guidance_bar.show()
        self.guidance_btn.show()
        self._apply_view_mode()

    def _on_guidance_btn_clicked(self):
        # Handle new conversational guidance input mode
        if self._guidance_input_active and isinstance(self._guidance_input_payload, dict):
            payload = self._guidance_input_payload
            self._guidance_input_payload = None
            self._guidance_input_active = False
            self._mark_last_guidance_step_completed()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            self.input_field.setPlaceholderText("Ask Pixel Pilot to do anything...")
            # Set "done" as the default input when clicking Next
            self._start_turn()
            self.add_activity_message("Planning next step...")
            payload["feedback"] = "done"
            if payload.get("event"):
                payload["event"].set()
            return
            
        # Handle legacy guidance button mode
        payload = self._guidance_payload
        self._guidance_payload = None
        self._guidance_active = False
        self._mark_last_guidance_step_completed()
        self.guidance_bar.hide()
        self.guidance_btn.hide()
        if isinstance(payload, dict):
            self._start_turn()
            self.add_activity_message("Planning next step...")
            payload["result"] = True
            payload["event"].set()

    def _on_continue_btn_clicked(self):
        """Handle continue button click."""
        if self._guidance_input_active and isinstance(self._guidance_input_payload, dict):
            payload = self._guidance_input_payload
            self._guidance_input_payload = None
            self._guidance_input_active = False
            self._mark_last_guidance_step_completed()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            self.input_field.setPlaceholderText("Ask Pixel Pilot to do anything...")
            
            self._start_turn()
            self.add_activity_message("Continuing task...")
            payload["feedback"] = "no"
            if payload.get("event"):
                payload["event"].set()


