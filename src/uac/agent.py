from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from ctypes import wintypes

import ctypes


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(CURRENT_DIR)
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from config import Config  # noqa: E402
from uac.ipc import ensure_ipc_root, load_request, load_response  # noqa: E402


DEBUG_LOG = str(ensure_ipc_root() / "agent.log")

KEYEVENTF_KEYUP = 0x0002
VK_RETURN = 0x0D
VK_LEFT = 0x25
VK_ESCAPE = 0x1B
VK_MENU = 0x12
VK_N = 0x4E
VK_Y = 0x59

user32 = ctypes.windll.user32


def log(message: str) -> None:
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{time.ctime()}: {message}\n")
    except Exception:
        pass


def key_down(vk: int) -> None:
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.keybd_event(vk, scan, 0, 0)


def key_up(vk: int) -> None:
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)


def press(vk: int) -> None:
    key_down(vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)
    key_up(vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)


def hotkey(modifier_vk: int, key_vk: int) -> None:
    key_down(modifier_vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)
    key_down(key_vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)
    key_up(key_vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)
    key_up(modifier_vk)
    time.sleep(Config.UAC_HELPER_KEY_PRESS_DELAY_SECONDS)


def capture_bmp(snapshot_path: str) -> bool:
    try:
        gdi32 = ctypes.windll.gdi32
        user32.SetProcessDPIAware()

        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)

        hdc = user32.GetDC(0)
        mem_dc = gdi32.CreateCompatibleDC(hdc)
        hbitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        gdi32.SelectObject(mem_dc, hbitmap)

        gdi32.BitBlt(mem_dc, 0, 0, width, height, hdc, 0, 0, 0x00CC0020)

        header_size = 54
        image_size = width * height * 4
        file_size = header_size + image_size

        bmp_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
        dib_header = struct.pack(
            "<IiiHHIIiiII",
            40,
            width,
            -height,
            1,
            32,
            0,
            image_size,
            0,
            0,
            0,
            0,
        )

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bi = BITMAPINFO()
        bi.biSize = 40
        bi.biWidth = width
        bi.biHeight = -height
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0

        buffer = ctypes.create_string_buffer(image_size)
        gdi32.GetDIBits(mem_dc, hbitmap, 0, height, buffer, ctypes.byref(bi), 0)

        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        with open(snapshot_path, "wb") as handle:
            handle.write(bmp_header)
            handle.write(dib_header)
            handle.write(buffer)

        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, hdc)

        log(f"Snapshot saved to {snapshot_path}")
        return True
    except Exception as exc:
        log(f"Screenshot Error: {exc}")
        return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request")
    parser.add_argument("--decision")
    return parser.parse_args()


def _decision_from_args(raw_decision: str) -> str:
    clean = str(raw_decision or "").strip().upper()
    if clean in {"ALLOW", "DENY"}:
        return clean
    return ""


def _execute_decision(decision: str) -> None:
    if decision == "ALLOW":
        log("Action: ALLOW (Alt+Y)")
        hotkey(VK_MENU, VK_Y)
        time.sleep(Config.UAC_HELPER_POST_ACTION_DELAY_SECONDS)
        press(VK_LEFT)
        press(VK_RETURN)
        return

    log("Action: DENY (Alt+N)")
    hotkey(VK_MENU, VK_N)
    time.sleep(Config.UAC_HELPER_POST_ACTION_DELAY_SECONDS)
    press(VK_ESCAPE)


def main() -> None:
    log("--- AGENT START ---")
    try:
        args = _parse_args()
        decision_arg = _decision_from_args(getattr(args, "decision", ""))

        if decision_arg:
            _execute_decision(decision_arg)
            log("--- AGENT FINISH ---")
            return

        request_arg = str(getattr(args, "request", "") or "").strip()
        if not request_arg:
            log("No --request or --decision provided. Exiting.")
            return

        request_payload = load_request(request_arg, max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS)
        if not request_payload:
            log("Invalid or stale UAC request. Exiting with DENY default.")
            return

        snapshot_path = str(request_payload.get("snapshot_path") or "").strip()
        if not snapshot_path:
            log("Missing snapshot path in UAC request. Exiting with DENY default.")
            return

        time.sleep(Config.UAC_HELPER_INITIAL_CAPTURE_DELAY_SECONDS)
        capture_bmp(snapshot_path)

        log("Waiting for secure UAC response...")
        response = None
        deadline = time.time() + float(Config.UAC_RESPONSE_TIMEOUT_SECONDS)
        while time.time() < deadline:
            response = load_response(
                request_payload,
                max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS,
            )
            if response:
                break
            time.sleep(Config.UAC_IPC_POLL_INTERVAL_SECONDS)

        command = "DENY"
        if not response:
            log("Timeout waiting for confirmed response. Defaulting to DENY.")
        else:
            allow = bool(response.get("allow"))
            user_confirmed = bool(response.get("user_confirmed"))
            if allow and user_confirmed:
                command = "ALLOW"
            else:
                command = "DENY"
            log(f"Received response: allow={allow} confirmed={user_confirmed}")

        _execute_decision(command)

        log("--- AGENT FINISH ---")
    except SystemExit:
        raise
    except Exception as exc:
        log(f"FATAL CRASH: {exc}")


if __name__ == "__main__":
    main()
