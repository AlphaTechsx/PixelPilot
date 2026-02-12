import ctypes
import os
import sys
import time
import logging
import threading
from contextlib import contextmanager
from ctypes import wintypes
from typing import Optional
from PIL import Image
from PIL import ImageDraw

logger = logging.getLogger("pixelpilot.desktop")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_SWITCHDESKTOP = 0x0100
DESKTOP_CREATEMENU = 0x0004
DESKTOP_HOOKCONTROL = 0x0008
DESKTOP_JOURNALPLAYBACK = 0x0020
DESKTOP_JOURNALRECORD = 0x0010
DESKTOP_READOBJECTS = 0x0001
GENERIC_ALL = 0x10000000
PROCESS_TERMINATE = 0x0001

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]

class AgentDesktopManager:
    DEFAULT_DESKTOP_NAME = "PixelPilotAgent"

    def __init__(self, desktop_name: Optional[str] = None):
        self.desktop_name = desktop_name or self.DEFAULT_DESKTOP_NAME
        self._desktop_handle: Optional[int] = None
        self._original_desktop: Optional[int] = None
        self._lock = threading.Lock()
        self._created = False
        self._cursor_pos = (0, 0)
        self._tracked_pids: list[int] = []

    @property
    def is_created(self) -> bool:
        return self._created and self._desktop_handle is not None

    def create_desktop(self) -> bool:
        with self._lock:
            if self._created:
                return True
            try:
                self._desktop_handle = user32.OpenDesktopW(self.desktop_name, 0, False, GENERIC_ALL)
                if self._desktop_handle:
                    self._created = True
                    return True
                self._desktop_handle = user32.CreateDesktopW(self.desktop_name, None, None, 0, GENERIC_ALL, None)
                if not self._desktop_handle:
                    return False
                self._created = True
                return True
            except Exception:
                return False

    def _get_current_thread_desktop(self) -> Optional[int]:
        return user32.GetThreadDesktop(kernel32.GetCurrentThreadId())

    def switch_thread_to_desktop(self) -> bool:
        if not self.is_created:
            return False
        try:
            self._original_desktop = self._get_current_thread_desktop()
            if not user32.SetThreadDesktop(self._desktop_handle):
                return False
            return True
        except Exception:
            return False

    def restore_thread_desktop(self) -> bool:
        if self._original_desktop is None:
            return False
        try:
            if not user32.SetThreadDesktop(self._original_desktop):
                return False
            self._original_desktop = None
            return True
        except Exception:
            return False

    @contextmanager
    def thread_context(self):
        switched = False
        try:
            switched = self.switch_thread_to_desktop()
            if not switched:
                raise RuntimeError("Failed to switch")
            yield
        finally:
            if switched:
                self.restore_thread_desktop()

    def run_on_desktop(self, func, *args, **kwargs):
        if not self.is_created:
            return None
        
        current_desktop = self._get_current_thread_desktop()
        if current_desktop == self._desktop_handle:
            return func(*args, **kwargs)

        result = [None]
        error = [None]
        def worker():
            try:
                if not user32.SetThreadDesktop(self._desktop_handle):
                    return
                result[0] = func(*args, **kwargs)
            except Exception as e:
                error[0] = str(e)
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=5.0)
        return result[0]

    def capture_desktop(self) -> Optional[Image.Image]:
        try:
            return self.run_on_desktop(self._capture_current_desktop)
        except Exception:
            return None

    def capture_desktop_raw(self) -> Optional[tuple[bytes, int, int]]:
        try:
            return self.run_on_desktop(self._capture_current_desktop_raw)
        except Exception:
            return None

    def _capture_current_desktop(self) -> Optional[Image.Image]:
        try:
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            h_desktop_win = user32.GetDesktopWindow()
            hdc_screen = user32.GetDC(h_desktop_win)
            if not hdc_screen:
                return self._create_placeholder_image(width, height)
            try:
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                if not hdc_mem:
                    return self._create_placeholder_image(width, height)
                try:
                    hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                    if not hbitmap:
                        return self._create_placeholder_image(width, height)
                    try:
                        old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap)
                        success = self._composite_windows_capture(hdc_mem, width, height)
                        if not success:
                            success = user32.PrintWindow(h_desktop_win, hdc_mem, PW_RENDERFULLCONTENT)
                        if not success:
                            return self._create_placeholder_image(width, height)
                        bmi = BITMAPINFO()
                        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                        bmi.bmiHeader.biWidth = width
                        bmi.bmiHeader.biHeight = -height
                        bmi.bmiHeader.biPlanes = 1
                        bmi.bmiHeader.biBitCount = 32
                        bmi.bmiHeader.biCompression = BI_RGB
                        buffer_size = width * height * 4
                        buffer = ctypes.create_string_buffer(buffer_size)
                        if not gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS):
                            return None
                        img = Image.frombytes("RGBA", (width, height), buffer.raw, "raw", "BGRA")
                        
                        draw = ImageDraw.Draw(img)
                        cx, cy = self._cursor_pos
                        
                        draw.polygon([(cx+1, cy+1), (cx + 11, cy + 11), (cx+1, cy + 16)], fill="black")
                        draw.polygon([(cx, cy), (cx + 10, cy + 10), (cx, cy + 15)], fill="white", outline="black")
                        
                        return img
                    finally:
                        gdi32.DeleteObject(hbitmap)
                finally:
                    gdi32.DeleteDC(hdc_mem)
            finally:
                user32.ReleaseDC(h_desktop_win, hdc_screen)
        except Exception:
            return self._create_placeholder_image(1920, 1080)

    def _capture_current_desktop_raw(self) -> Optional[tuple[bytes, int, int]]:
        """Direct BGRA capture without PIL conversion."""
        try:
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            h_desktop_win = user32.GetDesktopWindow()
            hdc_screen = user32.GetDC(h_desktop_win)
            if not hdc_screen:
                return None
            try:
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                if not hdc_mem:
                    return None
                try:
                    hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                    if not hbitmap:
                        return None
                    try:
                        old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap)
                        success = self._composite_windows_capture(hdc_mem, width, height)
                        if not success:
                            success = user32.PrintWindow(h_desktop_win, hdc_mem, PW_RENDERFULLCONTENT)
                        if not success:
                            return None
                            
                        bmi = BITMAPINFO()
                        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                        bmi.bmiHeader.biWidth = width
                        bmi.bmiHeader.biHeight = -height
                        bmi.bmiHeader.biPlanes = 1
                        bmi.bmiHeader.biBitCount = 32
                        bmi.bmiHeader.biCompression = BI_RGB
                        buffer_size = width * height * 4
                        buffer = ctypes.create_string_buffer(buffer_size)
                        if gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS):
                            return (buffer.raw, width, height)
                        return None
                    finally:
                        gdi32.DeleteObject(hbitmap)
                finally:
                    gdi32.DeleteDC(hdc_mem)
            finally:
                user32.ReleaseDC(h_desktop_win, hdc_screen)
        except Exception:
            return None

    def _composite_windows_capture(self, hdc_dest: int, width: int, height: int) -> bool:
        windows_to_draw = []
        
        def enum_handler(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                title_buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title_buf, 512)
                title = title_buf.value
                
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                class_name = class_buf.value
                
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                
                if rect.right > rect.left and rect.bottom > rect.top:
                    windows_to_draw.append({
                        'hwnd': hwnd,
                        'title': title,
                        'class': class_name,
                        'rect': rect
                    })
            return True
            
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
        user32.EnumDesktopWindows(self._desktop_handle, WNDENUMPROC(enum_handler), 0)

        found_any = False
        for w in reversed(windows_to_draw):
            rect = w['rect']
            w_width = rect.right - rect.left
            w_height = rect.bottom - rect.top
            
            hdc_temp = gdi32.CreateCompatibleDC(hdc_dest)
            hbmp_temp = gdi32.CreateCompatibleBitmap(hdc_dest, w_width, w_height)
            old_bmp = gdi32.SelectObject(hdc_temp, hbmp_temp)
            
            try:
                if user32.PrintWindow(w['hwnd'], hdc_temp, PW_RENDERFULLCONTENT):
                    gdi32.BitBlt(hdc_dest, rect.left, rect.top, w_width, w_height, 
                               hdc_temp, 0, 0, SRCCOPY)
                    found_any = True
            finally:
                gdi32.SelectObject(hdc_temp, old_bmp)
                gdi32.DeleteObject(hbmp_temp)
                gdi32.DeleteDC(hdc_temp)
                
        return found_any

    def _create_placeholder_image(self, width: int, height: int) -> Image.Image:
        from PIL import ImageDraw
        img = Image.new("RGB", (width, height), color=(40, 44, 52))
        draw = ImageDraw.Draw(img)
        text = f"Agent Desktop: {self.desktop_name}\n(Empty or Loading...)"
        draw.text((width//2 - 100, height//2), text, fill=(171, 178, 191))
        return img

    def initialize_shell(self) -> bool:
        """
        Initializes a custom shell session on the Agent Desktop.
        Avoids explorer.exe to prevent session leakage and singleton issues.
        """
        logger.info(f"Initializing custom shell on {self.desktop_name}...")
        self._cleanup_legacy_shells()
        
        try:
            from config import Config
            project_root = str(getattr(Config, "PROJECT_ROOT", os.getcwd()))
        except Exception:
            project_root = os.getcwd()

        taskbar_path = f'"{sys.executable}" "{os.path.join(project_root, "src", "ui", "minimal_taskbar.py")}"'
        success = self.launch_process(taskbar_path, working_dir=project_root)
        
        if success:
            logger.info("Custom minimal taskbar launched successfully")
        else:
            logger.error("Failed to launch custom minimal taskbar")
            
        return success

    def _cleanup_legacy_shells(self):
        """Attempts to close lingering explorer windows from previous failed shell startups."""
        try:
            windows = self.list_windows()
            for w in windows:
                title = w['title'].lower()
                if any(x in title for x in ["documents", "file explorer", "this pc", "cmd.exe"]):
                    logger.info(f"Cleaning up legacy window: {w['title']}")
                    user32.PostMessageW(w['hwnd'], 0x0010, 0, 0) # WM_CLOSE
        except Exception as e:
            logger.debug(f"Error during legacy cleanup: {e}")

    def _ensure_focus(self):
        try:
            hwnd = self.get_foreground_window()
            if not hwnd or not user32.IsWindowVisible(hwnd):
                windows = self.list_windows()
                if windows:
                    target = next((w['hwnd'] for w in windows if "Shell" in w['title']), windows[0]['hwnd'])
                    user32.SetForegroundWindow(target)
                    user32.SetActiveWindow(target)
                    user32.SetFocus(target)
        except Exception:
            pass

    def get_foreground_window(self) -> Optional[int]:
        """Get the foreground window on the managed desktop."""
        return self.run_on_desktop(user32.GetForegroundWindow)

    def get_window_at_point(self, x: int, y: int) -> Optional[int]:
        def _get_win():
            point = wintypes.POINT(x, y)
            return user32.WindowFromPoint(point)
        return self.run_on_desktop(_get_win)

    def get_focused_window(self) -> Optional[int]:
        return self.run_on_desktop(user32.GetForegroundWindow)

    def set_foreground_window(self, hwnd: int) -> bool:
        def _set_fg():
            user32.ShowWindow(hwnd, 5) # SW_SHOW
            return user32.SetForegroundWindow(hwnd)
        return self.run_on_desktop(_set_fg)

    def set_cursor_pos(self, x: int, y: int):
        self._cursor_pos = (x, y)

    def get_cursor_pos(self) -> tuple[int, int]:
        return self._cursor_pos

    def launch_process(self, command: str, working_dir: Optional[str] = None) -> bool:
        if not self.is_created:
            return False
        try:
            class STARTUPINFO(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
                ]
            class PROCESS_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
                ]
            si = STARTUPINFO()
            si.cb = ctypes.sizeof(STARTUPINFO)
            si.lpDesktop = f"winsta0\\{self.desktop_name}"
            pi = PROCESS_INFORMATION()
            dwCreationFlags = 0x00000010 | 0x00000400
            success = kernel32.CreateProcessW(None, command, None, None, False, dwCreationFlags, None, working_dir, ctypes.byref(si), ctypes.byref(pi))
            if success:
                try:
                    if pi.dwProcessId:
                        self._tracked_pids.append(int(pi.dwProcessId))
                except Exception:
                    pass
                kernel32.CloseHandle(pi.hProcess)
                kernel32.CloseHandle(pi.hThread)
                return True
            return False
        except Exception:
            return False

    def terminate_tracked_processes(self) -> None:
        if not self._tracked_pids:
            return

        remaining = []
        for pid in self._tracked_pids:
            try:
                h_proc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if h_proc:
                    kernel32.TerminateProcess(h_proc, 1)
                    kernel32.CloseHandle(h_proc)
                else:
                    remaining.append(pid)
            except Exception:
                remaining.append(pid)

        self._tracked_pids = remaining

    def list_windows(self) -> list:
        windows = []
        def enum_handler(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                windows.append({"hwnd": hwnd, "title": buff.value})
            return True
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
        
        def _enumerate():
            user32.EnumDesktopWindows(self._desktop_handle, WNDENUMPROC(enum_handler), 0)
            return windows
            
        return self.run_on_desktop(_enumerate) or []

    def close_all_windows(self, timeout: float = 2.0) -> None:
        if not self._desktop_handle:
            return

        try:
            windows = self.list_windows()
            for w in windows:
                try:
                    user32.PostMessageW(w["hwnd"], 0x0010, 0, 0)
                except Exception:
                    pass

            if timeout > 0:
                time.sleep(timeout)
        except Exception:
            pass

    def close(self):
        with self._lock:
            if self._desktop_handle:
                try:
                    self.close_all_windows(timeout=0.5)
                except Exception:
                    pass
                try:
                    self.terminate_tracked_processes()
                except Exception:
                    pass
                try:
                    user32.CloseDesktop(self._desktop_handle)
                except Exception:
                    pass
                finally:
                    self._desktop_handle = None
                    self._created = False

    def __enter__(self):
        self.create_desktop()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
