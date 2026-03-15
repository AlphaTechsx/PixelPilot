from PySide6.QtCore import QObject, Signal
import threading

class GuiAdapter(QObject):
    system_message_received = Signal(str)
    user_message_received = Signal(str)
    output_message_received = Signal(str)
    error_message_received = Signal(str)
    activity_message_received = Signal(str)
    final_answer_received = Signal(str)
    guidance_next_requested = Signal(str, object)
    guidance_input_requested = Signal(object) 
    workspace_changed = Signal(str)
    live_transcript_received = Signal(str, str, bool)
    live_session_state_received = Signal(str)
    live_action_state_received = Signal(object)
    live_audio_level_received = Signal(float)
    assistant_audio_level_received = Signal(float)
    live_availability_received = Signal(bool, str)
    live_voice_active_received = Signal(bool)
    

    confirmation_requested = Signal(str, str, object) 
    input_requested = Signal(str, str, object)
    screenshot_prep_requested = Signal(object)
    screenshot_restore_requested = Signal(object)
    click_through_requested = Signal(bool, object)

    def __init__(self):
        super().__init__()
        self.current_mode = None 

    def add_system_message(self, message):
        self.system_message_received.emit(message)

    def add_user_message(self, message):
        self.user_message_received.emit(message)
        
    def add_output_message(self, message):
        self.output_message_received.emit(message)
        
    def add_error_message(self, message):
        self.error_message_received.emit(message)

    def add_activity_message(self, message):
        self.activity_message_received.emit(message)

    def add_final_answer(self, message: str):
        self.final_answer_received.emit(message)

    def request_guidance_next(self, label: str, payload: dict):
        self.guidance_next_requested.emit(label, payload)

    def request_guidance_input(self, payload: dict):
        """Request user input for conversational guidance mode."""
        self.guidance_input_requested.emit(payload)

    def notify_workspace_changed(self, workspace: str):
        self.workspace_changed.emit(workspace)

    def update_live_transcript(self, speaker: str, text: str, final: bool):
        self.live_transcript_received.emit(speaker, text, final)

    def update_live_session_state(self, state: str):
        self.live_session_state_received.emit(state)

    def update_live_action_state(self, payload: dict):
        self.live_action_state_received.emit(payload)

    def update_live_audio_level(self, level: float):
        self.live_audio_level_received.emit(float(level))

    def update_assistant_audio_level(self, level: float):
        self.assistant_audio_level_received.emit(float(level))

    def update_live_availability(self, available: bool, reason: str):
        self.live_availability_received.emit(bool(available), str(reason or ""))

    def update_live_voice_active(self, active: bool):
        self.live_voice_active_received.emit(bool(active))

    def ask_confirmation(self, title, text):
        event = threading.Event()
        payload = {'result': False, 'event': event}
        self.confirmation_requested.emit(title, text, payload)
        event.wait()
        return payload['result']

    def ask_input(self, title, question):
        event = threading.Event()
        payload = {'result': None, 'event': event}
        self.input_requested.emit(title, question, payload)
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
