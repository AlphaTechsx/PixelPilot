from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from config import Config
from .gui_adapter import GuiAdapter
from .main_window import MainWindow
from .state_models import UiActionsBridge, UiStateStore

logger = logging.getLogger("pixelpilot.ui.widget_shell")


class WidgetDesktopShell(QObject):
    def __init__(
        self,
        state_store: UiStateStore,
        actions: UiActionsBridge,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.state_store = state_store
        self.actions = actions
        self.window = MainWindow(enable_backdrop=Config.GUI_ENABLE_GLASS_BACKDROP)
        self._preview_source = None
        self._adapter: GuiAdapter | None = None

        self._bind_widget_actions()
        self._bind_state()
        self._sync_initial_state()

    def connect_adapter(self, adapter: GuiAdapter) -> None:
        if self._adapter is adapter:
            return
        self._adapter = adapter
        adapter.system_message_received.connect(self.window.chat_widget.add_system_message)
        adapter.output_message_received.connect(self.window.chat_widget.add_output_message)
        adapter.error_message_received.connect(self.window.chat_widget.add_error_message)
        adapter.user_message_received.connect(self.window.chat_widget.add_user_message)
        adapter.activity_message_received.connect(self.window.chat_widget.add_activity_message)
        adapter.final_answer_received.connect(self.window.chat_widget.add_final_answer)
        adapter.live_transcript_received.connect(self.window.chat_widget.on_live_transcript)
        adapter.live_action_state_received.connect(self.window.chat_widget.on_live_action_state)
        adapter.live_session_state_received.connect(self.window.chat_widget.set_live_session_state)
        adapter.live_audio_level_received.connect(self.window.chat_widget.on_live_audio_level)
        adapter.assistant_audio_level_received.connect(self.window.chat_widget.on_assistant_audio_level)
        adapter.live_availability_received.connect(self.window.chat_widget.set_live_availability)
        adapter.live_voice_active_received.connect(self.window.chat_widget.set_live_voice_active)

        adapter.activity_message_received.connect(self.window.minimized_notch.add_activity_message)
        adapter.error_message_received.connect(self.window.minimized_notch.add_error_message)
        adapter.final_answer_received.connect(self.window.minimized_notch.add_final_answer)
        adapter.live_transcript_received.connect(self.window.minimized_notch.on_live_transcript)
        adapter.live_action_state_received.connect(self.window.minimized_notch.on_live_action_state)
        adapter.live_session_state_received.connect(self.window.minimized_notch.set_live_session_state)
        adapter.live_audio_level_received.connect(self.window.minimized_notch.on_live_audio_level)
        adapter.assistant_audio_level_received.connect(self.window.minimized_notch.on_assistant_audio_level)
        adapter.live_availability_received.connect(self.window.minimized_notch.set_live_availability)
        adapter.live_voice_active_received.connect(self.window.minimized_notch.set_live_voice_active)

    def echo_user_command(self, text: str) -> None:
        self.window.minimized_notch.on_user_command(text)

    def _bind_widget_actions(self) -> None:
        chat = self.window.chat_widget
        chat.command_received.connect(self.actions.submitCommand)
        chat.mode_changed.connect(lambda mode: self.actions.selectMode(getattr(mode, "value", mode)))
        chat.vision_changed.connect(self.actions.selectVision)
        chat.live_mode_changed.connect(self.actions.requestLiveMode)
        chat.live_voice_toggled.connect(self.actions.requestLiveVoice)
        chat.logout_btn.clicked.connect(self.actions.requestLogout)
        chat.close_btn.clicked.connect(self.actions.requestQuit)

        chat.agent_view_visibility_changed.connect(self._sync_agent_view_request_from_widget)
        chat.expand_btn.clicked.connect(lambda: QTimer.singleShot(0, self._sync_expanded_from_widget))
        chat.minimize_btn.clicked.connect(lambda: QTimer.singleShot(0, self._sync_background_hidden_from_widget))

    def _bind_state(self) -> None:
        self.state_store.operationModeChanged.connect(self._apply_operation_mode)
        self.state_store.visionModeChanged.connect(self._apply_vision_mode)
        self.state_store.workspaceChanged.connect(self._apply_workspace)
        self.state_store.liveEnabledChanged.connect(self._apply_live_enabled)
        self.state_store.expandedChanged.connect(self._apply_expanded)
        self.state_store.agentViewEnabledChanged.connect(self._apply_agent_view_enabled)
        self.state_store.agentViewRequestedChanged.connect(self._apply_agent_view_requested)
        self.state_store.agentViewVisibleChanged.connect(self.refresh_agent_preview_visibility)

    def _sync_initial_state(self) -> None:
        self.state_store.set_expanded(self.window.expanded)
        self.state_store.set_background_hidden(False)
        self.state_store.set_click_through_enabled(self.window.click_through_enabled)
        self._apply_operation_mode()
        self._apply_vision_mode()
        self._apply_workspace()
        self._apply_live_enabled()
        self._apply_agent_view_enabled()

    def _apply_operation_mode(self) -> None:
        self.window.chat_widget.set_operation_mode(Config.get_mode(self.state_store.operationMode))

    def _apply_vision_mode(self) -> None:
        self.window.chat_widget.set_vision_mode(self.state_store.visionMode)

    def _apply_workspace(self) -> None:
        self.window.chat_widget.set_workspace_status(self.state_store.workspace)
        self.refresh_agent_preview_visibility()

    def _apply_live_enabled(self) -> None:
        self.window.chat_widget.set_live_enabled(self.state_store.liveEnabled)

    def _apply_expanded(self) -> None:
        if self.window.expanded != self.state_store.expanded:
            self.window.set_expanded(self.state_store.expanded)
        self.refresh_agent_preview_visibility()

    def _apply_agent_view_enabled(self) -> None:
        self.window.chat_widget.set_agent_view_enabled(self.state_store.agentViewEnabled)
        self.refresh_agent_preview_visibility()

    def _apply_agent_view_requested(self) -> None:
        self.window.chat_widget.set_agent_view_requested(self.state_store.agentViewRequested)
        self.refresh_agent_preview_visibility()

    def _sync_agent_view_request_from_widget(self) -> None:
        self.state_store.set_agent_view_requested(self.window.chat_widget.should_show_agent_view())

    def _sync_expanded_from_widget(self) -> None:
        self.state_store.set_expanded(self.window.expanded)

    def _sync_background_hidden_from_widget(self) -> None:
        self.state_store.set_background_hidden(bool(getattr(self.window, "_background_hidden", False)))
        self.refresh_agent_preview_visibility()

    def show(self) -> None:
        self.window.show()
        self.state_store.set_background_hidden(False)
        self.refresh_agent_preview_visibility()

    def hide(self) -> None:
        self.window.hide()
        self.refresh_agent_preview_visibility()

    def close(self) -> None:
        self.window.close()

    def isVisible(self) -> bool:
        return self.window.isVisible()

    def setWindowIcon(self, icon) -> None:
        self.window.setWindowIcon(icon)

    def windowIcon(self):
        return self.window.windowIcon()

    def dialog_parent(self):
        return self.window

    def shortcut_host(self):
        return self.window

    def restore_from_background(self) -> None:
        self.window.restore_from_background()
        self.state_store.set_background_hidden(False)
        self.refresh_agent_preview_visibility()

    def minimize_to_background(self) -> None:
        self.window.minimize_to_background()
        self.state_store.set_background_hidden(True)
        self.refresh_agent_preview_visibility()

    def toggle_background_visibility(self) -> None:
        self.window.toggle_background_visibility()
        self.state_store.set_background_hidden(bool(getattr(self.window, "_background_hidden", False)))
        self.refresh_agent_preview_visibility()

    def toggle_expand(self) -> None:
        self.window.toggle_expand()
        self.state_store.set_expanded(self.window.expanded)
        self.refresh_agent_preview_visibility()

    def set_click_through_enabled(self, enable: bool) -> None:
        self.window.set_click_through_enabled(bool(enable))
        self.state_store.set_click_through_enabled(self.window.click_through_enabled)

    def click_through_enabled(self) -> bool:
        return bool(self.window.click_through_enabled)

    def background_hidden(self) -> bool:
        return bool(getattr(self.window, "_background_hidden", False))

    def attach_agent_preview_source(self, source) -> None:
        self._preview_source = source
        self.window.chat_widget.set_agent_preview_source(source)
        self.state_store.set_agent_preview_available(bool(source))
        if source and getattr(source, "is_created", False):
            try:
                sidecar = self.window.ensure_sidecar()
                sidecar.set_capture_source(source)
            except Exception:
                logger.exception("Failed to attach widget sidecar preview source")
        self.refresh_agent_preview_visibility()

    def refresh_agent_preview_visibility(self) -> None:
        should_show = bool(
            self._preview_source
            and getattr(self._preview_source, "is_created", False)
            and self.state_store.workspace == "agent"
            and self.state_store.agentViewVisible
            and self.window.isVisible()
            and not self.background_hidden()
        )
        if getattr(self.window, "sidecar", None) is None and not should_show:
            self.state_store.set_sidecar_visible(False)
            return
        sidecar = self.window.ensure_sidecar()
        if self._preview_source and getattr(self._preview_source, "is_created", False):
            sidecar.set_capture_source(self._preview_source)
        if should_show:
            sidecar.show()
            sidecar.reattach()
        else:
            sidecar.hide()
        self.state_store.set_sidecar_visible(should_show)

    def prepare_for_screenshot(self) -> dict[str, bool]:
        payload = {
            "restore_main_window": bool(self.window.isVisible() and not self.background_hidden()),
            "restore_minimized_notch": bool(self.background_hidden()),
        }
        if payload["restore_minimized_notch"]:
            self.window.minimized_notch.hide_notch()
        if payload["restore_main_window"]:
            self.window.hide()
        return payload

    def restore_after_screenshot(self, payload: dict[str, bool]) -> None:
        if bool(payload.get("restore_main_window", False)):
            self.window.show()
            self.state_store.set_background_hidden(False)
        elif bool(payload.get("restore_minimized_notch", False)):
            self.minimize_to_background()
