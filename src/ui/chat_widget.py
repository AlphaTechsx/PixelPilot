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
    QMenu,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QUrl, QTimer, QEvent
from PySide6.QtGui import QAction, QActionGroup, QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtSvgWidgets import QSvgWidget

from services.audio import AudioService
from .animated_mic_button import AnimatedMicButton
from .animated_live_button import AnimatedLiveButton
from .workspace_badge_button import WorkspaceBadgeButton
from .sidecar_preview import EmbeddedAgentPreview
from config import OperationMode, Config


DEFAULT_INPUT_PROMPT = "Ask Pixy to do anything..."
LISTENING_INPUT_PROMPT = "Listening..."
LIVE_IDLE_INPUT_PROMPT = "Type or speak to Gemini Live..."
LIVE_VOICE_INPUT_PROMPT = "Live voice active..."
GUIDANCE_FINAL_PROMPT = "Click Done to finish or type a reply..."
GUIDANCE_INPUT_PROMPT = "Type 'done', ask a question, or describe what happened..."
DETAILS_COLLAPSED_ICON = "\u25A2"
DETAILS_EXPANDED_ICON = "\u2750"
MINIMIZE_ICON = "\u2212"
SETTINGS_ICON = "\u2699"


class ChatWidget(QWidget):
    command_received = Signal(str)
    mode_changed = Signal(object)
    vision_changed = Signal(str)
    live_mode_changed = Signal(bool)
    live_voice_toggled = Signal(bool)
    agent_view_visibility_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.audio_service = AudioService()
        self.audio_service.text_received.connect(self.on_speech_text)
        self.audio_service.status_changed.connect(self.on_listening_status)
        self.audio_service.level_changed.connect(self.on_audio_level)

        self._agent_panel_width = 380
        self._agent_panel_min_width = 300
        self._agent_panel_max_width = 720
        self._agent_resizing = False
        self._agent_resize_start_x = 0
        self._agent_resize_start_width = self._agent_panel_width

        self.setup_ui()
        self.apply_styles()

        self.send_btn.clicked.connect(self.send_message)
        self.input_field.returnPressed.connect(self.send_message)
        self.input_field.textEdited.connect(self._on_input_text_edited)
        self.mic_btn.clicked.connect(self.toggle_listening)
        self.guidance_btn.clicked.connect(self._on_guidance_btn_clicked)
        self.mode_combo.currentIndexChanged.connect(self.update_mode_tooltip)
        self.mode_combo.currentIndexChanged.connect(self._emit_mode_changed)
        self.vision_combo.currentIndexChanged.connect(self.update_vision_tooltip)
        self.vision_combo.currentIndexChanged.connect(self._emit_vision_changed)
        self.workspace_badge.clicked.connect(self._toggle_agent_view)
        self.live_btn.clicked.connect(self._emit_live_mode_changed)
        self.settings_btn.clicked.connect(self._show_settings_menu)

        self.view_mode = "bar_only"
        self._agent_view_enabled = False
        self._agent_view_requested = False
        self._agent_preview_source = None

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
        self._live_guidance_active: bool = False
        self._live_available = False
        self._live_enabled = False
        self._live_voice_active = False
        self._live_session_state = "disconnected"
        self._live_stream_ids: dict[str, str] = {}
        self._thinking_status_text = ""
        self._reply_status_text = ""
        self._user_audio_level = 0.0
        self._assistant_audio_level = 0.0
        self._assistant_streaming_active = False
        self._mic_visual_state = "idle"
        self._mode_actions: dict[str, QAction] = {}
        self._vision_actions: dict[str, QAction] = {}

        self._build_settings_menu()

        self.set_view_mode("bar_only")
        self.update_mode_tooltip()
        self.update_vision_tooltip()

        self.set_vision_mode("ROBO" if Config.USE_ROBOTICS_EYE else "OCR")
        self.set_workspace_status(Config.DEFAULT_WORKSPACE)
        self.set_live_availability(Config.LIVE_MODE_AVAILABLE, "")
        self.set_live_enabled(False)
        self.set_live_session_state("disconnected")

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
        self._sync_settings_menu()
        self._sync_live_guidance_state()
        self._refresh_input_placeholder()
        self._apply_view_mode()

    def _emit_mode_changed(self):
        text = self.mode_combo.currentText().strip().upper()
        self._sync_live_guidance_state()
        self._refresh_input_placeholder()
        self._apply_view_mode()
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
        self._sync_settings_menu()

    def set_live_availability(self, available: bool, reason: str = ""):
        self._live_available = bool(available)
        self.live_btn.setEnabled(self._live_available)
        if self._live_available:
            self.live_btn.setToolTip("Enable Gemini Live mode")
        else:
            self.live_btn.setChecked(False)
            self._live_enabled = False
            self._live_voice_active = False
            self._live_session_state = "disconnected"
            self._assistant_audio_level = 0.0
            self._assistant_streaming_active = False
            self.live_state_badge.setText("DISCONNECTED")
            self.live_state_badge.setProperty("state", "disconnected")
            self.live_state_badge.style().unpolish(self.live_state_badge)
            self.live_state_badge.style().polish(self.live_state_badge)
            self.live_btn.setToolTip(reason or "Gemini Live mode unavailable")
        self._sync_live_guidance_state()
        self._refresh_live_controls()
        self._refresh_mic_visual_state()
        self._apply_view_mode()

    def set_live_enabled(self, enabled: bool):
        target = bool(enabled and self._live_available)
        self._live_enabled = target
        if target and self.audio_service.is_listening:
            self.audio_service.stop_listening()
        if not target:
            self._live_voice_active = False
            self._user_audio_level = 0.0
            self._assistant_audio_level = 0.0
            self._assistant_streaming_active = False
        try:
            self.live_btn.blockSignals(True)
            self.live_btn.setChecked(target)
        finally:
            self.live_btn.blockSignals(False)
        self._sync_live_guidance_state()
        self._refresh_live_controls()
        self._refresh_mic_visual_state()
        self._apply_view_mode()

    def set_live_session_state(self, state: str):
        state_key = (state or "disconnected").strip().lower() or "disconnected"
        self._live_session_state = state_key
        self.live_state_badge.setText(state_key.upper())
        self.live_state_badge.setProperty("state", state_key)
        self.live_state_badge.style().unpolish(self.live_state_badge)
        self.live_state_badge.style().polish(self.live_state_badge)
        self._update_live_state_status(state_key)
        self._refresh_live_controls()
        self._apply_view_mode()

    def set_live_voice_active(self, active: bool):
        self._live_voice_active = bool(active and self._live_enabled)
        if not self._live_voice_active:
            self._user_audio_level = 0.0
        self._refresh_live_controls()
        self._refresh_mic_visual_state()
        self._apply_view_mode()

    def _live_button_presentation(self) -> tuple[str, str]:
        if not self._live_available:
            return ("disabled", self.live_btn.toolTip() or "Gemini Live unavailable")
        if not self._live_enabled:
            return ("off", "Enable Gemini Live mode")

        state_key = (self._live_session_state or "disconnected").strip().lower()
        if state_key == "disconnected":
            return ("ready", "Gemini Live is enabled and ready")
        if state_key == "connecting":
            return ("connecting", "Gemini Live is connecting")
        if state_key == "listening":
            return ("connected", "Disable Gemini Live mode")
        if state_key == "thinking":
            return ("thinking", "Gemini Live is thinking")
        if state_key == "waiting":
            return ("waiting", "Gemini Live is waiting for an action")
        if state_key == "acting":
            return ("acting", "Gemini Live is acting")
        if state_key == "interrupted":
            return ("interrupted", "Gemini Live was interrupted")
        return ("connected", "Disable Gemini Live mode")

    def _apply_live_button_state(self):
        state, tooltip = self._live_button_presentation()
        self.live_btn.set_visual_state(state)
        self.live_btn.setProperty("state", state)
        self.live_btn.setToolTip(tooltip)
        self.live_btn.style().unpolish(self.live_btn)
        self.live_btn.style().polish(self.live_btn)

    def _refresh_live_controls(self):
        self._apply_live_button_state()
        if self._live_enabled and self._live_voice_active:
            self.mic_btn.setToolTip("Stop live voice session")
        elif self._live_enabled:
            self.mic_btn.setToolTip("Start live voice session")
        elif not self.audio_service.is_listening:
            self.mic_btn.setToolTip("Start listening")
        else:
            self.mic_btn.setToolTip("Stop listening")
        self._refresh_input_placeholder()

    def _emit_live_mode_changed(self):
        enabled = bool(self.live_btn.isChecked())
        if not enabled and self._live_voice_active:
            self.live_voice_toggled.emit(False)
            self._live_voice_active = False
        self._live_enabled = enabled and self._live_available
        self._sync_live_guidance_state()
        self.live_mode_changed.emit(self._live_enabled)
        self._refresh_live_controls()
        self._apply_view_mode()

    def _is_guidance_mode_selected(self) -> bool:
        return self.mode_combo.currentText().strip().upper() == "GUIDANCE"

    def _is_live_guidance_mode(self) -> bool:
        return bool(self._live_enabled and self._is_guidance_mode_selected())

    def _clear_guidance_state(self, *, cancel_pending: bool) -> None:
        payload = self._guidance_payload
        if cancel_pending and isinstance(payload, dict):
            payload["cancelled"] = True
            payload["result"] = False
            event = payload.get("event")
            if event is not None:
                event.set()
        self._guidance_payload = None
        self._guidance_active = False

        input_payload = self._guidance_input_payload
        if cancel_pending and isinstance(input_payload, dict):
            input_payload["cancelled"] = True
            event = input_payload.get("event")
            if event is not None:
                event.set()
        self._guidance_input_payload = None
        self._guidance_input_active = False

        self.guidance_bar.hide()
        self.guidance_btn.hide()
        self.continue_btn.hide()

    def _sync_live_guidance_state(self) -> None:
        active = self._is_live_guidance_mode()
        if active and not self._live_guidance_active:
            self._clear_guidance_state(cancel_pending=True)
        self._live_guidance_active = active

    def set_workspace_status(self, workspace: str):
        key = (workspace or "").strip().lower()
        if key not in {"user", "agent"}:
            key = "user"

        self.workspace_badge.set_workspace(key)
        self.set_agent_view_enabled(key == "agent")

    def set_expanded(self, expanded: bool):
        self.set_view_mode("extended" if expanded else "bar_only")

    def set_agent_view_enabled(self, enabled: bool):
        was_enabled = self._agent_view_enabled
        was_requested = self._agent_view_requested
        self._agent_view_enabled = bool(enabled)
        if not self._agent_view_enabled:
            self._agent_view_requested = False
        else:
            self._agent_view_requested = True
            
        self._refresh_workspace_badge()
        self._apply_view_mode()
        if was_enabled != self._agent_view_enabled or was_requested != self._agent_view_requested:
            self.agent_view_visibility_changed.emit()

    def set_agent_preview_source(self, desktop_manager_or_none):
        self._agent_preview_source = desktop_manager_or_none
        if desktop_manager_or_none is None:
            self._agent_view_requested = False
        self._apply_view_mode()
        self.agent_view_visibility_changed.emit()

    def _toggle_agent_view(self):
        if not self._agent_view_enabled:
            return
        self._agent_view_requested = not self._agent_view_requested
        self._apply_view_mode()
        self.agent_view_visibility_changed.emit()

    def can_toggle_agent_view(self) -> bool:
        return self._agent_view_enabled

    def should_show_agent_view(self) -> bool:
        return bool(self._agent_view_enabled and self._agent_view_requested)

    def _refresh_workspace_badge(self, show_agent: bool = False):
        workspace = self.workspace_badge.workspace()
        is_agent = workspace == "agent"
        is_clickable = bool(is_agent and self._agent_view_enabled)
        is_active = bool(is_clickable and show_agent)

        self.workspace_badge.set_clickable(is_clickable)
        self.workspace_badge.set_agent_view_shown(is_active)
        self.workspace_badge.setProperty("clickable", is_clickable)
        self.workspace_badge.setProperty("agentView", "shown" if is_active else "hidden")

        if workspace == "agent":
            if is_clickable:
                tooltip = (
                    "Current workspace: Agent Desktop. Click to hide the agent view"
                    if is_active
                    else "Current workspace: Agent Desktop. Click to show the agent view"
                )
            else:
                tooltip = "Current workspace: Agent Desktop"
        else:
            tooltip = "Current workspace: User Desktop"
        self.workspace_badge.setToolTip(tooltip)

    def _emit_vision_changed(self):
        text = self.vision_combo.currentText().strip().upper()
        if text == "ROBO":
            self.vision_changed.emit("robo")
        elif text == "OCR":
            self.vision_changed.emit("ocr")
        else:
            self.vision_changed.emit("robo")

    def _build_settings_menu(self):
        self.settings_menu = QMenu(self)
        self.settings_menu.setObjectName("settingsMenu")
        self.settings_menu.setStyleSheet(
            """
            QMenu {
                background: #f8fafc;
                color: #1f2937;
                border: 1px solid #d3dce8;
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::separator {
                height: 1px;
                background: #e2e8f0;
                margin: 6px 2px;
            }
            QMenu::item {
                padding: 7px 14px;
                border-radius: 8px;
            }
            QMenu::item:selected {
                background: #e2e8f0;
            }
        """
        )

        self.settings_menu.addSection("Mode")
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        mode_entries = [
            ("GUIDANCE", "Guidance"),
            ("SAFE", "Safe"),
            ("AUTO", "Auto"),
        ]
        for key, label in mode_entries:
            action = self.settings_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=key: self._select_mode_from_menu(value))
            mode_group.addAction(action)
            self._mode_actions[key] = action

        self.settings_menu.addSeparator()
        self.settings_menu.addSection("Vision")
        vision_group = QActionGroup(self)
        vision_group.setExclusive(True)
        vision_entries = [
            ("ROBO", "Robo"),
            ("OCR", "OCR"),
        ]
        for key, label in vision_entries:
            action = self.settings_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=key: self._select_vision_from_menu(value))
            vision_group.addAction(action)
            self._vision_actions[key] = action

        self.settings_menu.addSeparator()
        signout_action = self.settings_menu.addAction("Sign Out")
        signout_action.triggered.connect(self.logout_btn.click)
        self._sync_settings_menu()

    def _show_settings_menu(self):
        self._sync_settings_menu()
        anchor = self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomLeft())
        self.settings_menu.exec(anchor)

    def _sync_settings_menu(self):
        mode_key = self.mode_combo.currentText().strip().upper()
        for key, action in self._mode_actions.items():
            action.setChecked(key == mode_key)

        vision_key = self.vision_combo.currentText().strip().upper()
        for key, action in self._vision_actions.items():
            action.setChecked(key == vision_key)

    def _select_mode_from_menu(self, mode_key: str):
        target = (mode_key or "").strip().upper()
        index_map = {"GUIDANCE": 0, "SAFE": 1, "AUTO": 2}
        if target not in index_map:
            return
        self.mode_combo.setCurrentIndex(index_map[target])
        self._sync_settings_menu()

    def _select_vision_from_menu(self, vision_key: str):
        target = (vision_key or "").strip().upper()
        index_map = {"ROBO": 0, "OCR": 1}
        if target not in index_map:
            return
        self.vision_combo.setCurrentIndex(index_map[target])
        self._sync_settings_menu()

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(0)

        self.extended_panel = QFrame()
        self.extended_panel.setObjectName("extendedPanel")
        ep = QVBoxLayout(self.extended_panel)
        ep.setContentsMargins(10, 10, 10, 10)
        ep.setSpacing(8)

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

        self.workspace_badge = WorkspaceBadgeButton()
        self.workspace_badge.setObjectName("workspaceBadge")
        self.workspace_badge.setToolTip("Current workspace")
        self.workspace_badge.setCursor(Qt.CursorShape.ArrowCursor)
        self.workspace_badge.setFixedSize(40, 32)

        self.live_btn = AnimatedLiveButton()
        self.live_btn.setObjectName("liveToggleBtn")
        self.live_btn.setCheckable(True)
        self.live_btn.setToolTip("Enable Gemini Live mode")
        self.live_btn.setFixedSize(42, 32)

        self.live_state_badge = QLabel("DISCONNECTED")
        self.live_state_badge.setObjectName("liveStateBadge")
        self.live_state_badge.setToolTip("Gemini Live session state")
        self.live_state_badge.hide()

        self.logout_btn = QPushButton("Sign Out")
        self.logout_btn.setObjectName("logoutLink")
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.setToolTip("Sign out and switch accounts")

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

        self.compact_stop_btn = QPushButton("Stop")
        self.compact_stop_btn.setObjectName("compactStopBtn")
        self.compact_stop_btn.setFixedHeight(28)
        self.compact_stop_btn.setVisible(False)
        self.compact_stop_btn.setToolTip("Stop voice session")
        self.compact_stop_btn.clicked.connect(self._stop_voice_session)
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

        self.agent_panel = QFrame(self)
        self.agent_panel.setObjectName("agentPanel")
        ap = QVBoxLayout(self.agent_panel)
        ap.setContentsMargins(0, 0, 0, 0)
        ap.setSpacing(6)

        self.agent_panel_title = QLabel("Agent Desktop View")
        self.agent_panel_title.setObjectName("sectionTitle")
        ap.addWidget(self.agent_panel_title)

        self.agent_preview = EmbeddedAgentPreview(self, fps=Config.SIDECAR_PREVIEW_FPS)
        ap.addWidget(self.agent_preview)
        self.agent_panel.setMinimumWidth(self._agent_panel_min_width)
        self.agent_panel.setFixedWidth(self._agent_panel_width)
        self.agent_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.agent_panel.hide()

        self.agent_resize_handle = QFrame(self)
        self.agent_resize_handle.setObjectName("agentResizeHandle")
        self.agent_resize_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self.agent_resize_handle.setToolTip("Drag to resize agent view")
        self.agent_resize_handle.hide()
        self.agent_resize_handle.installEventFilter(self)

        self.extended_panel.setMinimumWidth(360)

        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        self.top_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.input_field.setPlaceholderText(DEFAULT_INPUT_PROMPT)

        self.mic_btn = AnimatedMicButton()
        self.mic_btn.setObjectName("micBtn")
        self.mic_btn.setFixedSize(40, 30)
        self.mic_btn.setToolTip("Start listening")

        self.send_btn = QPushButton("Go")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(38, 30)

        i.addWidget(self.input_field, 1)
        i.addWidget(self.mic_btn)
        i.addWidget(self.send_btn)

        self.expand_btn = QPushButton(DETAILS_COLLAPSED_ICON)
        self.expand_btn.setObjectName("expandBtn")
        self.expand_btn.setFixedHeight(32)
        self.expand_btn.setToolTip("Show process details")

        self.settings_btn = QPushButton(SETTINGS_ICON)
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setFixedSize(40, 32)
        self.settings_btn.setToolTip("Mode, vision, and account settings")

        self.minimize_btn = QPushButton(MINIMIZE_ICON)
        self.minimize_btn.setObjectName("minimizeBtn")
        self.minimize_btn.setFixedHeight(32)
        self.minimize_btn.setToolTip("Hide to background")

        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedHeight(32)

        h.addWidget(self.logo)
        h.addWidget(self.input_shell, 1)
        h.addWidget(self.workspace_badge)
        h.addWidget(self.live_btn)
        h.addWidget(self.settings_btn)
        h.addWidget(self.minimize_btn)
        h.addWidget(self.expand_btn)
        h.addWidget(self.close_btn)

        self.extended_panel.setVisible(False)

        self.top_row = QWidget()
        top_row_layout = QHBoxLayout(self.top_row)
        top_row_layout.setContentsMargins(0, 0, 0, 0)
        top_row_layout.setSpacing(0)
        top_row_layout.addWidget(self.top_bar, 1)

        layout.addWidget(self.top_row)
        layout.addWidget(self.extended_panel, 1)
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
            QPushButton#sendBtn {
                background: #eef2f7;
                border: 1px solid #d5dce7;
                border-radius: 10px;
                color: #334155;
                font: 600 11px 'Segoe UI', sans-serif;
            }
            QPushButton#sendBtn:hover { background: #e2e8f0; border-color: #bcc7d6; }
            AnimatedMicButton#micBtn { background: transparent; border: none; }
            QPushButton#expandBtn, QPushButton#settingsBtn, QPushButton#minimizeBtn, QPushButton#closeBtn {
                background: #ffffff;
                border: 1px solid #d2dae5;
                border-radius: 10px;
                color: #273345;
                font: 600 12px 'Segoe UI', sans-serif;
                padding: 0 10px;
            }
            QPushButton#expandBtn:hover, QPushButton#settingsBtn:hover, QPushButton#minimizeBtn:hover {
                background: #f1f5f9;
                border-color: #bfcbdc;
            }
            QPushButton#settingsBtn {
                font: 700 16px 'Segoe UI Symbol', 'Segoe UI', sans-serif;
                padding: 0;
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
            QFrame#agentResizeHandle {
                background: #d2dae5;
                border-radius: 3px;
            }
            QFrame#agentResizeHandle:hover {
                background: #94a3b8;
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
            WorkspaceBadgeButton#workspaceBadge { background: transparent; border: none; }
            AnimatedLiveButton#liveToggleBtn { background: transparent; border: none; }
            QLabel#liveStateBadge {
                background: #f8fafc;
                color: #475569;
                border: 1px solid #d5dce7;
                border-radius: 8px;
                padding: 2px 8px;
                font: 700 10px 'Segoe UI', sans-serif;
                qproperty-alignment: 'AlignCenter';
            }
            QLabel#liveStateBadge[state="connected"], QLabel#liveStateBadge[state="listening"] {
                background: #ecfdf3;
                color: #166534;
                border-color: #bbf7d0;
            }
            QLabel#liveStateBadge[state="connecting"], QLabel#liveStateBadge[state="thinking"], QLabel#liveStateBadge[state="waiting"] {
                background: #eff6ff;
                color: #1d4ed8;
                border-color: #bfdbfe;
            }
            QLabel#liveStateBadge[state="acting"], QLabel#liveStateBadge[state="interrupted"] {
                background: #fff7ed;
                color: #c2410c;
                border-color: #fed7aa;
            }
            QLabel#liveStateBadge[state="disconnected"] {
                background: #f8fafc;
                color: #475569;
                border-color: #d5dce7;
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
            QTextEdit#chatDisplay {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #d9e2ec;
                border-radius: 10px;
                font: 14px 'Segoe UI', sans-serif;
                padding: 10px;
            }
            QLineEdit#inputField::placeholder { color: #64748b; }
            QLabel#inputHint { color: #64748b; font: 12px 'Segoe UI', sans-serif; padding: 2px 2px; }
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

    def _guidance_placeholder_text(self) -> str:
        if self._is_live_guidance_mode():
            return ""
        if not self._guidance_input_active or not isinstance(self._guidance_input_payload, dict):
            return ""
        if self._guidance_input_payload.get("final"):
            return GUIDANCE_FINAL_PROMPT
        return GUIDANCE_INPUT_PROMPT

    def _idle_placeholder_text(self) -> str:
        if self._live_enabled and self._live_voice_active:
            return LIVE_VOICE_INPUT_PROMPT
        if self._live_enabled:
            return LIVE_IDLE_INPUT_PROMPT
        if self.audio_service.is_listening:
            return LISTENING_INPUT_PROMPT
        return DEFAULT_INPUT_PROMPT

    def _refresh_input_placeholder(self) -> None:
        placeholder = (
            self._guidance_placeholder_text()
            or self._reply_status_text
            or self._thinking_status_text
            or self._idle_placeholder_text()
        )
        self.input_field.setPlaceholderText(placeholder)

    def _clear_transient_status(self) -> None:
        self._thinking_status_text = ""
        self._reply_status_text = ""
        self._refresh_input_placeholder()

    def _set_thinking_status(self, text: str | None) -> None:
        clean = " ".join(str(text or "").split())
        self._thinking_status_text = clean
        self._refresh_input_placeholder()

    def _clear_thinking_status(self) -> None:
        self._thinking_status_text = ""
        self._refresh_input_placeholder()

    def _set_reply_status(self, text: str | None) -> None:
        clean = " ".join(str(text or "").split())
        self._reply_status_text = clean
        self._refresh_input_placeholder()

    def _clear_reply_status(self) -> None:
        self._reply_status_text = ""
        self._refresh_input_placeholder()

    def _assistant_visual_active(self) -> bool:
        return self._assistant_streaming_active or self._assistant_audio_level > 0.02

    def _refresh_mic_visual_state(self) -> None:
        if not self.mic_btn.isEnabled():
            state = "disabled"
            level = 0.0
        elif self._assistant_visual_active():
            state = "speaking_assistant"
            level = self._assistant_audio_level
        elif self._live_enabled and self._live_voice_active:
            state = "listening_user"
            level = self._user_audio_level
        elif self.audio_service.is_listening:
            state = "listening_user"
            level = self._user_audio_level
        else:
            state = "idle"
            level = 0.0

        self._mic_visual_state = state
        self.mic_btn.set_visual_state(state)
        self.mic_btn.set_level(level)

    def _update_live_state_status(self, state_key: str) -> None:
        state = (state_key or "").strip().lower()
        status_map = {
            "connecting": "Connecting to Gemini Live...",
            "thinking": "Thinking...",
            "waiting": "Waiting for the current action...",
            "acting": "Working on the task...",
            "interrupted": "Interrupted. Waiting for your next instruction...",
        }
        if self._reply_status_text:
            return
        if state in status_map:
            self._set_thinking_status(status_map[state])
            return
        if state in {"listening", "disconnected"}:
            self._clear_thinking_status()

    def _on_input_text_edited(self, _text: str) -> None:
        self._clear_transient_status()

    def _start_turn(self, *, reset_status: bool = True):
        self._turn_active = True
        self._thinking_id = None
        self._assistant_streaming_active = False
        self._assistant_audio_level = 0.0
        if reset_status:
            self._clear_transient_status()
        self._refresh_mic_visual_state()

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
                        self._set_thinking_status(append_line)
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
            self._refresh_input_placeholder()
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

        alignment = Qt.AlignmentFlag.AlignLeft
        bubble_bg = QColor("#00000000")
        text_color = QColor("#2563eb")
        font_weight = -1
        italic = False

        if kind_key == "user":
            alignment = Qt.AlignmentFlag.AlignRight
            text_color = QColor("#1d4ed8")
        elif kind_key == "assistant":
            alignment = Qt.AlignmentFlag.AlignLeft
            text_color = QColor("#2563eb")
        elif kind_key == "system":
            alignment = Qt.AlignmentFlag.AlignLeft
            text_color = QColor("#3b82f6")
            italic = True
        elif kind_key == "output":
            alignment = Qt.AlignmentFlag.AlignLeft
            text_color = QColor("#1d4ed8")
        elif kind_key == "error":
            alignment = Qt.AlignmentFlag.AlignLeft
            text_color = QColor("#dc2626")
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
            text_color = QColor("#60a5fa")
            bubble_bg = QColor("#00000000")
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
            body_char.setForeground(QColor("#2563eb"))
            body_char.setBackground(QColor("#00000000"))
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

    def _stop_voice_session(self):
        if self._live_enabled:
            self.live_voice_toggled.emit(False)
            self.set_live_voice_active(False)
            return
        self.audio_service.stop_listening()

    def toggle_listening(self):
        if self._live_enabled:
            target = not self._live_voice_active
            self.live_voice_toggled.emit(target)
            self.set_live_voice_active(target)
            self._apply_view_mode()
            return

        if self.audio_service.is_listening:
            self.audio_service.stop_listening()
        else:
            self.audio_service.start_listening()

    def on_listening_status(self, listening):
        if self._live_enabled:
            return
        if listening:
            self.mic_btn.setToolTip("Stop listening")
        else:
            self.mic_btn.setToolTip("Start listening")
            self._user_audio_level = 0.0
        self._refresh_input_placeholder()
        self._refresh_mic_visual_state()
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
        if self._is_live_guidance_mode():
            return False
        if not self._guidance_input_active:
            return False
        if not isinstance(self._guidance_input_payload, dict):
            return False

        payload = self._guidance_input_payload
        self._guidance_input_payload = None
        self._guidance_input_active = False
        self._refresh_input_placeholder()

        payload["feedback"] = text
        event = payload.get("event")
        if event is not None:
            event.set()

        return True

    def _send_guidance_feedback(self, text: str) -> bool:
        """Handle legacy guidance button feedback."""
        if self._is_live_guidance_mode():
            return False
        if not self._guidance_active:
            return False
        if not isinstance(self._guidance_payload, dict):
            return False

        payload = self._guidance_payload
        self._guidance_payload = None
        self._guidance_active = False
        self.guidance_bar.hide()
        self.guidance_btn.hide()
        self._refresh_input_placeholder()

        payload["result"] = False
        payload["feedback"] = text
        event = payload.get("event")
        if event is not None:
            event.set()

        self._apply_view_mode()
        return True

    def on_audio_level(self, level):
        self._user_audio_level = max(0.0, float(level or 0.0))
        self._refresh_mic_visual_state()

    def on_live_audio_level(self, level: float):
        self._user_audio_level = max(0.0, float(level or 0.0))
        self._refresh_mic_visual_state()

    def on_assistant_audio_level(self, level: float):
        self._assistant_audio_level = max(0.0, float(level or 0.0))
        self._refresh_mic_visual_state()

    def send_to_agent(self, text):
        self.command_received.emit(text)

    def on_live_transcript(self, speaker: str, text: str, final: bool):
        clean = str(text or "").strip()
        if not clean:
            return
        self._hide_input_hint()
        kind = "user" if str(speaker or "").strip().lower() == "user" else "assistant"
        if kind == "user" and not self._live_voice_active and self._merge_into_latest_typed_user(clean):
            return
        stream_key = "user" if kind == "user" else "assistant"
        if stream_key not in self._live_stream_ids:
            self._start_turn(reset_status=(kind == "user"))
        if kind == "assistant":
            self._assistant_streaming_active = not bool(final)
            self._set_reply_status(clean)
            self._refresh_mic_visual_state()
        self._upsert_live_transcript(kind=kind, text=clean, final=bool(final))
        if kind == "assistant" and final:
            self._assistant_streaming_active = False
            self._refresh_mic_visual_state()

    def on_live_action_state(self, payload: dict):
        if not isinstance(payload, dict):
            return
        status = str(payload.get("status") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not message:
            return
        if not self._reply_status_text:
            self._set_thinking_status(message)
        label = f"Live action {status}: {message}" if status else f"Live action: {message}"
        self.add_activity_message(label)

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
        self._assistant_streaming_active = True
        self._set_reply_status("Composing response...")
        self._refresh_mic_visual_state()

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
            self._assistant_streaming_active = False
            self._refresh_mic_visual_state()
            return

        # Stream in chunks for performance
        chunk_size = 12
        if self._stream_pos >= len(self._stream_full_text):
            if self._stream_timer is not None:
                self._stream_timer.stop()
            self._assistant_streaming_active = False
            self._refresh_mic_visual_state()
            return

        next_pos = min(len(self._stream_full_text), self._stream_pos + chunk_size)
        new_text = self._stream_full_text[:next_pos]
        self._stream_pos = next_pos
        self._set_reply_status(new_text)

        for msg in self._chat_model:
            if msg.get("id") == self._stream_target_id:
                msg["text"] = new_text
                break

        self._render_chat()

        if self._stream_pos >= len(self._stream_full_text):
            self._assistant_streaming_active = False
            self._refresh_mic_visual_state()

    def _upsert_live_transcript(self, *, kind: str, text: str, final: bool):
        stream_key = "user" if kind == "user" else "assistant"
        msg_id = self._live_stream_ids.get(stream_key)
        if not msg_id:
            msg_id = str(uuid4())
            self._live_stream_ids[stream_key] = msg_id
            self._chat_model.append({"kind": kind, "id": msg_id, "text": text})
            self._render_chat()
        else:
            for msg in self._chat_model:
                if msg.get("id") == msg_id:
                    msg["text"] = text
                    break
            self._render_chat()

        if final:
            self._live_stream_ids.pop(stream_key, None)

    def _merge_into_latest_typed_user(self, text: str) -> bool:
        if not self._chat_model:
            return False
        last = self._chat_model[-1]
        if (last.get("kind") or "").strip().lower() != "user":
            return False

        existing = str(last.get("text") or "").strip()
        incoming = str(text or "").strip()
        if not existing or not incoming:
            return False

        if incoming == existing or incoming.startswith(existing):
            last["text"] = incoming
            self._render_chat()
            return True
        if existing.startswith(incoming):
            return True
        return False

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
        self._apply_view_mode()

    def _sync_top_bar_width(self, show_agent: bool) -> None:
        if not hasattr(self, "top_bar"):
            return
        if not show_agent:
            self.top_bar.setMinimumWidth(0)
            self.top_bar.setMaximumWidth(16777215)
            self.top_bar.updateGeometry()
            return

        margin = 12
        gap = 10
        max_bar = max(300, self.width() - (self._agent_panel_width + margin * 2 + gap))
        self.top_bar.setFixedWidth(max_bar)

    def _position_agent_panel(self) -> None:
        if not hasattr(self, "agent_panel") or not hasattr(self, "agent_resize_handle") or not hasattr(self, "top_bar"):
            return
        if not self.agent_panel.isVisible():
            self.agent_resize_handle.hide()
            return

        margin = 12
        gap = 8
        top_left = self.top_bar.mapTo(self, self.top_bar.rect().topLeft())
        panel_w = max(self._agent_panel_min_width, min(self._agent_panel_width, self._agent_panel_max_width))
        panel_h = max(220, min(340, self.height() - 24))
        x = max(margin, self.width() - panel_w - margin)
        y = top_left.y() + 6

        self.agent_panel.setGeometry(x, y, panel_w, panel_h)

        handle_w = 6
        handle_y = y + 24
        handle_h = max(64, panel_h - 36)
        self.agent_resize_handle.setGeometry(max(margin, x - handle_w - gap), handle_y, handle_w, handle_h)
        self.agent_resize_handle.raise_()
        self.agent_panel.raise_()
        self.agent_resize_handle.show()

    @staticmethod
    def _mouse_global_x(event) -> int:
        if hasattr(event, "globalPosition"):
            return int(event.globalPosition().x())
        if hasattr(event, "globalPos"):
            return int(event.globalPos().x())
        return 0

    def _apply_view_mode(self):
        listening = self._live_voice_active if self._live_enabled else self.audio_service.is_listening
        live_guidance = self._is_live_guidance_mode()
        expanded = self.view_mode == "extended"

        self.extended_panel.setVisible(expanded)
        self.expand_btn.setText(DETAILS_EXPANDED_ICON if expanded else DETAILS_COLLAPSED_ICON)
        self.expand_btn.setToolTip("Hide process details" if expanded else "Show process details")

        # Sidecar preview is now owned by MainWindow; keep embedded preview hidden.
        show_agent = bool(self._agent_view_enabled and self._agent_view_requested)
        self.agent_panel.hide()
        self.agent_resize_handle.hide()
        if self._agent_resizing:
            self._agent_resizing = False
            try:
                self.agent_resize_handle.releaseMouse()
            except Exception:
                pass
        self.agent_preview.set_active(False)
        self._refresh_workspace_badge(show_agent)

        if not expanded:
            self.compact_stop_btn.hide()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            return

        if listening:
            self.chat_display.show()
            self.input_hint.hide()
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
        self.compact_stop_btn.hide()

        if live_guidance:
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
        elif self._guidance_active or self._guidance_input_active:
            self.guidance_bar.show()
            self.guidance_btn.show()
            show_continue = bool(
                self._guidance_input_active
                and isinstance(self._guidance_input_payload, dict)
                and self._guidance_input_payload.get("show_continue")
            )
            self.continue_btn.setVisible(show_continue)
        else:
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

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
        if self._is_live_guidance_mode():
            if isinstance(payload, dict):
                payload["cancelled"] = True
                payload["result"] = False
                event = payload.get("event")
                if event is not None:
                    event.set()
            self._clear_guidance_state(cancel_pending=False)
            self._refresh_input_placeholder()
            self._apply_view_mode()
            return
        text = (label or "Next").strip() or "Next"
        self._guidance_payload = payload
        self._guidance_active = True
        self.guidance_btn.setText(text)
        self.guidance_btn.setEnabled(True)
        self.guidance_bar.show()
        self.guidance_btn.show()
        self._apply_view_mode()

    def hide_guidance_button(self):
        self._clear_guidance_state(cancel_pending=False)
        self._refresh_input_placeholder()
        self._apply_view_mode()

    def show_guidance_input(self, payload: dict):
        """Enable conversational guidance input mode.
        
        The next message from the user will be sent to the guidance session
        instead of starting a new agent task. Also shows a "Next" button.
        """
        if self._is_live_guidance_mode():
            if isinstance(payload, dict):
                payload["cancelled"] = True
                event = payload.get("event")
                if event is not None:
                    event.set()
            self._clear_guidance_state(cancel_pending=False)
            self._refresh_input_placeholder()
            self._apply_view_mode()
            return
        self._guidance_input_payload = payload
        self._guidance_input_active = True
        label = (payload.get("label") or "Next").strip() or "Next"
        self.guidance_btn.setText(label)
        self.guidance_btn.setEnabled(True)
        
        if payload.get("show_continue"):
            self.continue_btn.show()
        else:
            self.continue_btn.hide()
            
        self.guidance_bar.show()
        self.guidance_btn.show()
        self._refresh_input_placeholder()
        self._apply_view_mode()

    def _on_guidance_btn_clicked(self):
        if self._is_live_guidance_mode():
            return
        # Handle new conversational guidance input mode
        if self._guidance_input_active and isinstance(self._guidance_input_payload, dict):
            payload = self._guidance_input_payload
            self._guidance_input_payload = None
            self._guidance_input_active = False
            self._mark_last_guidance_step_completed()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            self._refresh_input_placeholder()
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
        if self._is_live_guidance_mode():
            return
        if self._guidance_input_active and isinstance(self._guidance_input_payload, dict):
            payload = self._guidance_input_payload
            self._guidance_input_payload = None
            self._guidance_input_active = False
            self._mark_last_guidance_step_completed()
            self.guidance_bar.hide()
            self.guidance_btn.hide()
            self.continue_btn.hide()
            self._refresh_input_placeholder()
            
            self._start_turn()
            self.add_activity_message("Continuing task...")
            payload["feedback"] = "no"
            if payload.get("event"):
                payload["event"].set()


