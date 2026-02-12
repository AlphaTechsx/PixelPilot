import ctypes
import time
from ctypes import wintypes

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVED = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000


def _get_screen_size():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def move_to(x: int, y: int, desktop_manager=None):
    if desktop_manager:
        desktop_manager.set_cursor_pos(x, y)
        return
        
    width, height = _get_screen_size()
    norm_x = int((x * 65536) / width) + 1
    norm_y = int((y * 65536) / height) + 1
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.mi = MouseInput(
        norm_x, norm_y, 0, MOUSEEVENTF_MOVED | MOUSEEVENTF_ABSOLUTE, 0, ctypes.pointer(extra)
    )
    command = Input(ctypes.c_ulong(INPUT_MOUSE), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(command), ctypes.sizeof(command))


def click(desktop_manager=None):
    if desktop_manager:
        x, y = desktop_manager.get_cursor_pos()
        hwnd = desktop_manager.get_window_at_point(x, y)
        if hwnd:
            desktop_manager.set_foreground_window(hwnd)
            user32 = ctypes.windll.user32
            
            point = wintypes.POINT(x, y)
            user32.ScreenToClient(hwnd, ctypes.byref(point))
            lparam = (point.y << 16) | (point.x & 0xFFFF)
            
            user32.PostMessageW(hwnd, 0x0201, 0x0001, lparam) 
            time.sleep(0.05)
            user32.PostMessageW(hwnd, 0x0202, 0, lparam) 
        return
        
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(INPUT_MOUSE), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))
    time.sleep(0.05)
    ii_.mi = MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(INPUT_MOUSE), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def click_at(x: int, y: int, desktop_manager=None):
    move_to(x, y, desktop_manager=desktop_manager)
    time.sleep(0.1)
    click(desktop_manager=desktop_manager)


if __name__ == "__main__":
    print("Testing mouse move...")
    time.sleep(2)
    move_to(500, 500)
    print("Moved to 500, 500")
    time.sleep(1)
    click()
    print("Clicked")
