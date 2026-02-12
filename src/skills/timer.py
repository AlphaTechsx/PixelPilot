from .base import BaseSkill
import os


class TimerSkill(BaseSkill):
    name = "Timer"

    def __init__(self):
        super().__init__()
        self.register_method("timer", self.open_timer)
        self.register_method("alarm", self.open_alarm)
        self.register_method("stopwatch", self.open_stopwatch)

    def open_timer(self, desktop_manager=None):
        try:
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process("cmd /c start ms-clock:timer")
                return "Opened Windows Clock (Timer tab)." if success else "Failed to open timer"
            os.system("start ms-clock:timer")
            return "Opened Windows Clock (Timer tab). Please set the time manually."
        except Exception as e:
            return f"Error opening timer: {e}"

    def open_alarm(self, desktop_manager=None):
        try:
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process("cmd /c start ms-clock:alarm")
                return "Opened Windows Clock (Alarm tab)." if success else "Failed to open alarm"
            os.system("start ms-clock:alarm")
            return "Opened Windows Clock (Alarm tab)."
        except Exception as e:
            return f"Error opening alarm: {e}"

    def open_stopwatch(self, desktop_manager=None):
        try:
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process("cmd /c start ms-clock:stopwatch")
                return "Opened Windows Clock (Stopwatch tab)." if success else "Failed to open stopwatch"
            os.system("start ms-clock:stopwatch")
            return "Opened Windows Clock (Stopwatch tab)."
        except Exception as e:
            return f"Error opening stopwatch: {e}"
