from .base import BaseSkill
import keyboard
import ctypes


class SystemSkill(BaseSkill):
    name = "System"

    def __init__(self):
        super().__init__()
        self.register_method("volume", self.set_volume)
        self.register_method("lock", self.lock_screen)
        self.register_method("minimize", self.minimize_all)
        self.register_method("settings", self.open_settings)

    def set_volume(self, action):
        try:
            if action == "up":
                for _ in range(3):
                    keyboard.send("volume up")
                return "Increased volume"
            elif action == "down":
                for _ in range(3):
                    keyboard.send("volume down")
                return "Decreased volume"
            elif action == "mute":
                keyboard.send("volume mute")
                return "Toggled mute"
            else:
                return f"Unknown volume action: {action}"
        except Exception as e:
            return f"Error setting volume: {e}"

    def lock_screen(self):
        try:
            ctypes.windll.user32.LockWorkStation()
            return "Locked workstation"
        except Exception as e:
            return f"Error locking screen: {e}"

    def minimize_all(self):
        try:
            keyboard.send("win+d")
            return "Toggled Desktop (Minimize/Restore All)"
        except Exception as e:
            return f"Error minimizing: {e}"

    def open_settings(self, page=None, desktop_manager=None):
        try:
            uri = "ms-settings:"
            if page:
                uri += page
            if desktop_manager and desktop_manager.is_created:
                success = desktop_manager.launch_process(f'cmd /c start "" "{uri}"')
                return f"Opened Settings ({page or 'Home'})" if success else "Failed to open settings"
            import os
            os.system(f"start {uri}")
            return f"Opened Settings ({page or 'Home'})"
        except Exception as e:
            return f"Error opening settings: {e}"
