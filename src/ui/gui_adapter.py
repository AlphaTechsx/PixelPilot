from PySide6.QtCore import QObject, Signal
import threading

from .state_models import MessageFeedModel, UiStateStore

class GuiAdapter(QObject):
    system_message_received = Signal(str)
    user_message_received = Signal(str)
    output_message_received = Signal(str)
    error_message_received = Signal(str)
    activity_message_received = Signal(str)
    final_answer_received = Signal(str)
    workspace_changed = Signal(str)
    live_transcript_received = Signal(str, str, bool)
    live_session_state_received = Signal(str)
    live_action_state_received = Signal(object)
    live_audio_level_received = Signal(float)
    assistant_audio_level_received = Signal(float)
    live_availability_received = Signal(bool, str)
    live_voice_active_received = Signal(bool)
    

    confirmation_requested = Signal(str, str, object) 
    screenshot_prep_requested = Signal(object)
    screenshot_restore_requested = Signal(object)
    click_through_requested = Signal(bool, object)

    def __init__(
        self,
        *,
        ui_state_store: UiStateStore | None = None,
        message_feed_model: MessageFeedModel | None = None,
    ):
        super().__init__()
        self.current_mode = None
        self.ui_state_store = ui_state_store
        self.message_feed_model = message_feed_model

    def add_system_message(self, message):
        if self.message_feed_model is not None:
            self.message_feed_model.add_system_message(message)
        self.system_message_received.emit(message)

    def add_user_message(self, message):
        if self.message_feed_model is not None:
            self.message_feed_model.add_user_message(message)
        self.user_message_received.emit(message)
        
    def add_output_message(self, message):
        if self.message_feed_model is not None:
            self.message_feed_model.add_output_message(message)
        self.output_message_received.emit(message)
        
    def add_error_message(self, message):
        if self.message_feed_model is not None:
            self.message_feed_model.add_error_message(message)
        self.error_message_received.emit(message)

    def add_activity_message(self, message):
        if self.message_feed_model is not None:
            self.message_feed_model.add_activity_message(message)
        self.activity_message_received.emit(message)

    def add_final_answer(self, message: str):
        if self.message_feed_model is not None:
            self.message_feed_model.add_final_answer(message)
        self.final_answer_received.emit(message)

    def notify_workspace_changed(self, workspace: str):
        if self.ui_state_store is not None:
            self.ui_state_store.set_workspace(workspace)
        self.workspace_changed.emit(workspace)

    def update_live_transcript(self, speaker: str, text: str, final: bool):
        if self.message_feed_model is not None:
            self.message_feed_model.update_live_transcript(speaker, text, final)
        self.live_transcript_received.emit(speaker, text, final)

    def update_live_session_state(self, state: str):
        if self.ui_state_store is not None:
            self.ui_state_store.set_live_session_state(state)
        self.live_session_state_received.emit(state)

    def update_live_action_state(self, payload: dict):
        self.live_action_state_received.emit(payload)

    def update_live_audio_level(self, level: float):
        if self.ui_state_store is not None:
            self.ui_state_store.set_user_audio_level(level)
        self.live_audio_level_received.emit(float(level))

    def update_assistant_audio_level(self, level: float):
        if self.ui_state_store is not None:
            self.ui_state_store.set_assistant_audio_level(level)
        self.assistant_audio_level_received.emit(float(level))

    def update_live_availability(self, available: bool, reason: str):
        if self.ui_state_store is not None:
            self.ui_state_store.set_live_availability(available, reason)
        self.live_availability_received.emit(bool(available), str(reason or ""))

    def update_live_voice_active(self, active: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_live_voice_active(active)
        self.live_voice_active_received.emit(bool(active))

    def set_operation_mode(self, mode):
        self.current_mode = mode
        if self.ui_state_store is not None:
            self.ui_state_store.set_operation_mode(mode)

    def set_vision_mode(self, mode: str):
        if self.ui_state_store is not None:
            self.ui_state_store.set_vision_mode(mode)

    def set_workspace(self, workspace: str):
        if self.ui_state_store is not None:
            self.ui_state_store.set_workspace(workspace)

    def set_live_enabled(self, enabled: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_live_enabled(enabled)

    def set_agent_view_enabled(self, enabled: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_agent_view_enabled(enabled)

    def set_agent_view_requested(self, requested: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_agent_view_requested(requested)

    def set_expanded(self, expanded: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_expanded(expanded)

    def set_background_hidden(self, hidden: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_background_hidden(hidden)

    def set_click_through_enabled(self, enabled: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_click_through_enabled(enabled)

    def set_agent_preview_available(self, available: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_agent_preview_available(available)

    def set_sidecar_visible(self, visible: bool):
        if self.ui_state_store is not None:
            self.ui_state_store.set_sidecar_visible(visible)

    def ask_confirmation(self, title, text):
        event = threading.Event()
        payload = {'result': False, 'event': event}
        self.confirmation_requested.emit(title, text, payload)
        event.wait()
        return payload['result']

    def prepare_for_screenshot(self):
        event = threading.Event()
        payload = {'event': event}
        self.screenshot_prep_requested.emit(payload)
        event.wait()

    def restore_after_screenshot(self):
        event = threading.Event()
        payload = {'event': event}
        self.screenshot_restore_requested.emit(payload)
        event.wait()

    def set_click_through(self, enable):
        event = threading.Event()
        payload = {'event': event}
        self.click_through_requested.emit(enable, payload)
        event.wait()
