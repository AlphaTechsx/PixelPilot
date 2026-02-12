import time
from typing import Optional
import ctypes
import pyautogui
import pyperclip

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1

try:
    import keyboard as _third_party_keyboard
except Exception:
    _third_party_keyboard = None


def add_hotkey(hotkey, callback, suppress: bool = False, timeout: Optional[float] = None):
    """Register a global hotkey using the third-party `keyboard` package if present."""
    if _third_party_keyboard:
        return _third_party_keyboard.add_hotkey(
            hotkey, callback, suppress=suppress, timeout=timeout
        )
    raise RuntimeError(
        "third-party 'keyboard' package is not available; install it for hotkey support"
    )


def send(keys, do_press: bool = True):
    """Send key presses via third-party `keyboard` if available."""
    if _third_party_keyboard:
        return _third_party_keyboard.send(keys, do_press=do_press)
    raise RuntimeError("third-party 'keyboard' package is not available; install it to use `send`")


class KeyboardController:
    KEY_MAPPING = {
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "esc": "esc",
        "escape": "esc",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "insert": "insert",
        "capslock": "capslock",
        "numlock": "numlock",
        "scrolllock": "scrolllock",
        "win": "win",
        "windows": "win",
        "cmd": "command",
        "command": "command",
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        **{f"f{i}": f"f{i}" for i in range(1, 13)},
    }

    VK_MAPPING = {
        "enter": 0x0D,
        "return": 0x0D,
        "tab": 0x09,
        "space": 0x20,
        "backspace": 0x08,
        "delete": 0x2E,
        "esc": 0x1B,
        "escape": 0x1B,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "win": 0x5B,
        "ctrl": 0x11,
        "alt": 0x12,
        "shift": 0x10,
    }

    def __init__(self):
        self.last_action_time = time.time()

    def type_text(self, text: str, interval: float = 0.05, desktop_manager=None) -> bool:
        if desktop_manager:
            hwnd = desktop_manager.get_focused_window()
            if not hwnd:
                x, y = desktop_manager.get_cursor_pos()
                hwnd = desktop_manager.get_window_at_point(x, y)
            if hwnd:
                user32 = ctypes.windll.user32
                for char in text:
                    user32.PostMessageW(hwnd, 0x0102, ord(char), 0)
                    if interval > 0:
                        time.sleep(interval)
                return True
            return False
        try:
            pyautogui.write(text, interval=interval)
            self.last_action_time = time.time()
            return True
        except Exception:
            return False

    def press_key(self, key: str, presses: int = 1, desktop_manager=None) -> bool:
        if desktop_manager:
            hwnd = desktop_manager.get_focused_window()
            if not hwnd:
                x, y = desktop_manager.get_cursor_pos()
                hwnd = desktop_manager.get_window_at_point(x, y)
            if hwnd:
                user32 = ctypes.windll.user32
                key_lower = key.lower()
                vk = self.VK_MAPPING.get(key_lower)
                if vk:
                    for _ in range(presses):
                        user32.PostMessageW(hwnd, 0x0100, vk, 0)
                        time.sleep(0.05)
                        user32.PostMessageW(hwnd, 0x0101, vk, 0)
                    return True
            return False
        try:
            key_lower = key.lower()
            actual_key = self.KEY_MAPPING.get(key_lower, key_lower)
            pyautogui.press(actual_key, presses=presses)
            self.last_action_time = time.time()
            return True
        except Exception:
            return False

    def key_combo(self, *keys: str, desktop_manager=None) -> bool:
        if desktop_manager:
            hwnd = desktop_manager.get_focused_window()
            if hwnd:
                user32 = ctypes.windll.user32
                vks = []
                for key in keys:
                    vk = self.VK_MAPPING.get(key.lower())
                    if vk:
                        vks.append(vk)
                        user32.PostMessageW(hwnd, 0x0100, vk, 0)
                        time.sleep(0.05)
                
                for vk in reversed(vks):
                    user32.PostMessageW(hwnd, 0x0101, vk, 0)
                    time.sleep(0.05)
                return True
            return False
        try:
            actual_keys = [self.KEY_MAPPING.get(key.lower(), key.lower()) for key in keys]
            pyautogui.hotkey(*actual_keys)
            self.last_action_time = time.time()
            return True
        except Exception:
            return False

    def hold_key(self, key: str, duration: float = 0.5, desktop_manager=None) -> bool:
        if desktop_manager:
            return desktop_manager.run_on_desktop(self.hold_key, key, duration)
        try:
            key_lower = key.lower()
            actual_key = self.KEY_MAPPING.get(key_lower, key_lower)
            pyautogui.keyDown(actual_key)
            time.sleep(duration)
            pyautogui.keyUp(actual_key)
            self.last_action_time = time.time()
            return True
        except Exception:
            return False

    def copy_to_clipboard(self, text: str) -> bool:
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            return False

    def paste_from_clipboard(self, desktop_manager=None) -> bool:
        try:
            return self.key_combo("ctrl", "v", desktop_manager=desktop_manager)
        except Exception:
            return False

    def get_clipboard_text(self) -> Optional[str]:
        try:
            return pyperclip.paste()
        except Exception:
            return None

    def select_all(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "a", desktop_manager=desktop_manager)

    def copy(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "c", desktop_manager=desktop_manager)

    def paste(self, desktop_manager=None) -> bool:
        return self.paste_from_clipboard(desktop_manager=desktop_manager)

    def cut(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "x", desktop_manager=desktop_manager)

    def undo(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "z", desktop_manager=desktop_manager)

    def redo(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "y", desktop_manager=desktop_manager)

    def save(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "s", desktop_manager=desktop_manager)

    def open_start_menu(self, desktop_manager=None) -> bool:
        return self.press_key("win", desktop_manager=desktop_manager)

    def alt_tab(self, desktop_manager=None) -> bool:
        return self.key_combo("alt", "tab", desktop_manager=desktop_manager)

    def close_window(self, desktop_manager=None) -> bool:
        return self.key_combo("alt", "f4", desktop_manager=desktop_manager)

    def new_tab(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "t", desktop_manager=desktop_manager)

    def close_tab(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "w", desktop_manager=desktop_manager)

    def refresh_page(self, desktop_manager=None) -> bool:
        return self.press_key("f5", desktop_manager=desktop_manager)

    def search(self, desktop_manager=None) -> bool:
        return self.key_combo("ctrl", "f", desktop_manager=desktop_manager)


keyboard_controller = KeyboardController()


def type_text(text: str, interval: float = 0.05, desktop_manager=None) -> bool:
    return keyboard_controller.type_text(text, interval, desktop_manager=desktop_manager)


def press_key(key: str, presses: int = 1, desktop_manager=None) -> bool:
    return keyboard_controller.press_key(key, presses, desktop_manager=desktop_manager)


def key_combo(*keys: str, desktop_manager=None) -> bool:
    return keyboard_controller.key_combo(*keys, desktop_manager=desktop_manager)


if __name__ == "__main__":
    print("Testing Keyboard Controller...")
    print("\n1. Testing key press (Enter)...")
    time.sleep(2)
    keyboard_controller.press_key("enter")

    print("\n2. Testing key combo (Ctrl+C)...")
    time.sleep(1)
    keyboard_controller.key_combo("ctrl", "c")

    print("\n3. Testing clipboard...")
    keyboard_controller.copy_to_clipboard("Hello from AI Agent!")
    time.sleep(1)
    print(f"Clipboard content: {keyboard_controller.get_clipboard_text()}")

    print("\n Keyboard Controller tests complete!")
