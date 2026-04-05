from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows hosts
    winreg = None


IS_WINDOWS = sys.platform.startswith("win")
logger = logging.getLogger("pixelpilot.uac.detection")

_PROMPT_LOG_LOCK = threading.Lock()
_LAST_PROMPT_ACTIVE: bool | None = None
_LAST_PROMPT_LOG_AT = 0.0
_PROMPT_ACTIVE_LOG_COOLDOWN_SECONDS = 3.0

# Token access rights and token information classes.
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
TOKEN_ELEVATION_TYPE = 18
TOKEN_INTEGRITY_LEVEL = 25

DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
INVALID_SESSION_ID = 0xFFFFFFFF

WIN_BUILTIN_ADMINISTRATORS_SID = 26
SECURITY_MAX_SID_SIZE = 68

SECURITY_MANDATORY_UNTRUSTED_RID = 0x00000000
SECURITY_MANDATORY_LOW_RID = 0x00001000
SECURITY_MANDATORY_MEDIUM_RID = 0x00002000
SECURITY_MANDATORY_HIGH_RID = 0x00003000
SECURITY_MANDATORY_SYSTEM_RID = 0x00004000
SECURITY_MANDATORY_PROTECTED_PROCESS_RID = 0x00005000

REG_PATH_ENABLE_LUA = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"


class TOKEN_ELEVATION_STRUCT(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", wintypes.LPVOID),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


if IS_WINDOWS:
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    user32 = ctypes.windll.user32

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    advapi32.CreateWellKnownSid.argtypes = [
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.CheckTokenMembership.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.CheckTokenMembership.restype = wintypes.BOOL

    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
else:
    kernel32 = None
    advapi32 = None
    user32 = None


def _read_enable_lua_registry_value() -> int | None:
    if not IS_WINDOWS or winreg is None:
        return None

    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_PATH_ENABLE_LUA, 0, access) as key:
            value, _kind = winreg.QueryValueEx(key, "EnableLUA")
        return int(value)
    except Exception:
        return None


def is_uac_enabled() -> bool | None:
    raw_value = _read_enable_lua_registry_value()
    if raw_value is None:
        return None
    return bool(raw_value)


def _open_current_process_token() -> wintypes.HANDLE | None:
    if not IS_WINDOWS:
        return None
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return None
    return token


def _close_handle(handle: wintypes.HANDLE | None) -> None:
    if not IS_WINDOWS or handle is None or not getattr(handle, "value", 0):
        return
    kernel32.CloseHandle(handle)


def _get_token_information(token: wintypes.HANDLE, info_class: int) -> Any | None:
    needed = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(needed))
    if needed.value <= 0:
        return None

    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(
        token,
        info_class,
        buffer,
        needed.value,
        ctypes.byref(needed),
    ):
        return None
    return buffer


def _token_is_elevated(token: wintypes.HANDLE) -> bool:
    buffer = _get_token_information(token, TOKEN_ELEVATION)
    if buffer is None:
        return False
    info = ctypes.cast(buffer, ctypes.POINTER(TOKEN_ELEVATION_STRUCT)).contents
    return bool(info.TokenIsElevated)


def _token_elevation_type(token: wintypes.HANDLE) -> str:
    buffer = _get_token_information(token, TOKEN_ELEVATION_TYPE)
    if buffer is None:
        return "unknown"

    raw_type = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    mapping = {
        1: "default",
        2: "full",
        3: "limited",
    }
    return mapping.get(int(raw_type), "unknown")


def _token_integrity_rid(token: wintypes.HANDLE) -> int | None:
    buffer = _get_token_information(token, TOKEN_INTEGRITY_LEVEL)
    if buffer is None:
        return None

    label = ctypes.cast(buffer, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
    sid = label.Label.Sid
    if not sid:
        return None

    sub_auth_count = advapi32.GetSidSubAuthorityCount(sid)
    if not sub_auth_count:
        return None
    count = int(sub_auth_count[0])
    if count <= 0:
        return None

    rid_ptr = advapi32.GetSidSubAuthority(sid, count - 1)
    if not rid_ptr:
        return None
    return int(rid_ptr[0])


def _map_integrity_rid(rid: int | None) -> str:
    if rid is None:
        return "unknown"
    if rid >= SECURITY_MANDATORY_PROTECTED_PROCESS_RID:
        return "protected_process"
    if rid >= SECURITY_MANDATORY_SYSTEM_RID:
        return "system"
    if rid >= SECURITY_MANDATORY_HIGH_RID:
        return "high"
    if rid >= SECURITY_MANDATORY_MEDIUM_RID:
        return "medium"
    if rid >= SECURITY_MANDATORY_LOW_RID:
        return "low"
    if rid >= SECURITY_MANDATORY_UNTRUSTED_RID:
        return "untrusted"
    return "unknown"


def _is_admin_member() -> bool:
    if not IS_WINDOWS:
        return False

    sid_buffer = ctypes.create_string_buffer(SECURITY_MAX_SID_SIZE)
    sid_size = wintypes.DWORD(len(sid_buffer))
    if not advapi32.CreateWellKnownSid(
        WIN_BUILTIN_ADMINISTRATORS_SID,
        None,
        sid_buffer,
        ctypes.byref(sid_size),
    ):
        return False

    is_member = wintypes.BOOL(False)
    if not advapi32.CheckTokenMembership(None, sid_buffer, ctypes.byref(is_member)):
        return False
    return bool(is_member.value)


def get_process_uac_state() -> dict[str, Any]:
    process_state: dict[str, Any] = {
        "isElevated": False,
        "isAdminMember": False,
        "elevationType": "unknown",
        "integrityLevel": "unknown",
        "integrityRid": None,
    }
    if not IS_WINDOWS:
        return process_state

    process_state["isAdminMember"] = _is_admin_member()

    token = _open_current_process_token()
    if token is None:
        return process_state

    try:
        process_state["isElevated"] = _token_is_elevated(token)
        process_state["elevationType"] = _token_elevation_type(token)
        rid = _token_integrity_rid(token)
        process_state["integrityRid"] = rid
        process_state["integrityLevel"] = _map_integrity_rid(rid)
    finally:
        _close_handle(token)

    return process_state


def _get_active_console_session_id() -> int | None:
    if not IS_WINDOWS:
        return None
    session_id = int(kernel32.WTSGetActiveConsoleSessionId())
    if session_id == INVALID_SESSION_ID:
        return None
    return session_id


def _get_input_desktop_name() -> str | None:
    name, _error_code = _get_input_desktop_details()
    return name


def _get_input_desktop_details() -> tuple[str | None, int | None]:
    if not IS_WINDOWS:
        return None, None

    ctypes.set_last_error(0)
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desktop:
        try:
            err = int(ctypes.get_last_error())
        except Exception:
            err = None
        if not err and kernel32 is not None:
            try:
                err = int(kernel32.GetLastError())
            except Exception:
                err = None
        return None, err

    try:
        needed = wintypes.DWORD(0)
        user32.GetUserObjectInformationW(desktop, UOI_NAME, None, 0, ctypes.byref(needed))
        if needed.value <= 0:
            return None, None

        char_count = max(1, needed.value // ctypes.sizeof(ctypes.c_wchar))
        buffer = ctypes.create_unicode_buffer(char_count)
        if not user32.GetUserObjectInformationW(
            desktop,
            UOI_NAME,
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(needed),
        ):
            return None, None
        name = str(buffer.value or "").strip()
        return name or None, None
    finally:
        user32.CloseDesktop(desktop)


def _process_id_to_session_id(pid: int) -> int | None:
    if not IS_WINDOWS:
        return None
    session_id = wintypes.DWORD(0)
    if not kernel32.ProcessIdToSessionId(wintypes.DWORD(int(pid)), ctypes.byref(session_id)):
        return None
    return int(session_id.value)


def _find_processes_named(process_name: str, *, session_id: int | None = None) -> list[int]:
    target = str(process_name or "").strip().lower()
    if not target:
        return []

    try:
        import psutil
    except Exception:
        return []

    matches: list[int] = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            name = str(proc.info.get("name") or "").strip().lower()
        except Exception:
            continue

        if pid <= 0 or name != target:
            continue
        if session_id is not None:
            proc_session_id = _process_id_to_session_id(pid)
            if proc_session_id is not None and proc_session_id != session_id:
                continue
        matches.append(pid)

    return sorted(set(matches))


def _log_prompt_detection(prompt_state: dict[str, Any]) -> None:
    global _LAST_PROMPT_ACTIVE, _LAST_PROMPT_LOG_AT

    active = bool(prompt_state.get("likelyPromptActive"))
    now = time.monotonic()
    should_log = False

    with _PROMPT_LOG_LOCK:
        if _LAST_PROMPT_ACTIVE is None or active != _LAST_PROMPT_ACTIVE:
            should_log = True
        elif active and (now - _LAST_PROMPT_LOG_AT) >= _PROMPT_ACTIVE_LOG_COOLDOWN_SECONDS:
            should_log = True

        if not should_log:
            return

        _LAST_PROMPT_ACTIVE = active
        _LAST_PROMPT_LOG_AT = now

    if active:
        logger.info(
            "UAC_DETECTOR prompt_active=true desktop=%s confidence=%s consent_pids=%s",
            str(prompt_state.get("inputDesktop") or "unknown"),
            str(prompt_state.get("confidence") or "unknown"),
            prompt_state.get("consentProcessPids") or [],
        )
    else:
        logger.debug(
            "UAC_DETECTOR prompt_active=false desktop=%s confidence=%s",
            str(prompt_state.get("inputDesktop") or "unknown"),
            str(prompt_state.get("confidence") or "unknown"),
        )


def get_uac_prompt_state() -> dict[str, Any]:
    active_session_id = _get_active_console_session_id()
    desktop_name, desktop_open_error = _get_input_desktop_details()
    secure_desktop_active = bool(desktop_name and desktop_name.lower() != "default")

    # Access denied while reading the input desktop frequently indicates the secure desktop is active.
    if not secure_desktop_active and int(desktop_open_error or 0) == 5:
        secure_desktop_active = True

    consent_pids = _find_processes_named("consent.exe", session_id=active_session_id)
    credential_broker_pids = _find_processes_named(
        "credentialuibroker.exe",
        session_id=active_session_id,
    )
    uac_ui_pids = sorted(set([*consent_pids, *credential_broker_pids]))
    uac_ui_present = bool(uac_ui_pids)

    confidence = "low"
    likely_prompt_active = False
    if uac_ui_present and secure_desktop_active:
        likely_prompt_active = True
        confidence = "high"
    elif uac_ui_present or secure_desktop_active:
        likely_prompt_active = True
        confidence = "medium"

    prompt_state: dict[str, Any] = {
        "activeConsoleSessionId": active_session_id,
        "inputDesktop": desktop_name,
        "inputDesktopOpenError": desktop_open_error,
        "secureDesktopActive": secure_desktop_active,
        "consentProcessPids": consent_pids,
        "consentProcessPresent": bool(consent_pids),
        "credentialUiBrokerPids": credential_broker_pids,
        "uacUiProcessPids": uac_ui_pids,
        "uacUiProcessPresent": uac_ui_present,
        "likelyPromptActive": likely_prompt_active,
        "confidence": confidence,
    }
    _log_prompt_detection(prompt_state)
    return prompt_state


def get_uac_state_snapshot() -> dict[str, Any]:
    raw_enable_lua = _read_enable_lua_registry_value()
    return {
        "isWindows": IS_WINDOWS,
        "supported": IS_WINDOWS,
        "uacEnabled": None if raw_enable_lua is None else bool(raw_enable_lua),
        "uacEnabledRawValue": raw_enable_lua,
        "process": get_process_uac_state(),
        "prompt": get_uac_prompt_state(),
    }


__all__ = [
    "get_process_uac_state",
    "get_uac_prompt_state",
    "get_uac_state_snapshot",
    "is_uac_enabled",
]
