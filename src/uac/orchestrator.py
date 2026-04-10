from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ctypes import wintypes

import ctypes


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(CURRENT_DIR)
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from config import Config  # noqa: E402
from uac.ipc import ensure_ipc_root, load_request, pending_request_paths  # noqa: E402


kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

MAXIMUM_ALLOWED = 0x02000000
PROCESS_QUERY_INFORMATION = 0x0400
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
CLAIM_TTL_SECONDS = 120.0

DEBUG_LOG = str(ensure_ipc_root() / "orchestrator.log")


def _api_host() -> str:
    return str(getattr(Config, "UAC_ORCHESTRATOR_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"


def _api_port() -> int:
    try:
        return int(getattr(Config, "UAC_ORCHESTRATOR_API_PORT", 8779) or 8779)
    except Exception:
        return 8779


def log_debug(message: str) -> None:
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{time.ctime()}: {message}\n")
    except Exception:
        pass


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_byte * 0),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def enable_privilege(privilege_name: str) -> bool:
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0020 | 0x0008,
            ctypes.byref(token),
        ):
            return False

        luid = wintypes.LARGE_INTEGER()
        if not advapi32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)):
            return False

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [
                ("Count", wintypes.DWORD),
                ("Luid", wintypes.LARGE_INTEGER),
                ("Attr", wintypes.DWORD),
            ]

        tp = TOKEN_PRIVILEGES(1, luid, 0x00000002)
        if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        return True
    except Exception:
        return False


def get_base_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_winlogon_pid(session_id: int) -> int | None:
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == -1:
        return None

    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

    if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
        kernel32.CloseHandle(snapshot)
        return None

    found_pid = None
    while True:
        if entry.szExeFile.lower() == "winlogon.exe":
            current_session = wintypes.DWORD()
            if kernel32.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(current_session)):
                if current_session.value == session_id:
                    found_pid = int(entry.th32ProcessID)
                    break
        if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
            break

    kernel32.CloseHandle(snapshot)
    return found_pid


def _build_agent_command(*, base_path: str, request_path: str | None = None, decision: str | None = None) -> str | None:
    clean_decision = str(decision or "").strip().upper()
    request = str(request_path or "").strip()
    if clean_decision and clean_decision not in {"ALLOW", "DENY"}:
        return None
    if not clean_decision and not request:
        return None

    decision_arg = f'--decision "{clean_decision}"' if clean_decision else ""
    request_arg = f'--request "{request}"' if request else ""
    arg_fragment = decision_arg or request_arg

    agent_candidates = [
        os.path.join(base_path, "agent", "agent.exe"),
        os.path.join(base_path, "dist", "agent.exe"),
    ]
    for agent_exe in agent_candidates:
        if os.path.exists(agent_exe):
            cmd_line = f'"{agent_exe}" {arg_fragment}'.strip()
            log_debug(f"Launching compiled agent: {cmd_line}")
            return cmd_line

    agent_script = os.path.join(base_path, "agent.py")
    python_exe = (
        sys.executable
        if not getattr(sys, "frozen", False)
        else os.path.join(base_path, "venv", "Scripts", "python.exe")
    )
    if not os.path.exists(python_exe):
        python_exe = "python.exe"
    cmd_line = f'"{python_exe}" "{agent_script}" {arg_fragment}'.strip()
    log_debug(f"Launching with Python: {cmd_line}")
    return cmd_line


def inject_agent_to_winlogon(
    session_id: int,
    request_path: str | None = None,
    decision: str | None = None,
) -> bool:
    winlogon_pid = get_winlogon_pid(session_id)
    if not winlogon_pid:
        log_debug(f"Could not find winlogon for session {session_id}")
        return False

    log_debug(f"Found WinLogon PID: {winlogon_pid}")
    process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, winlogon_pid)
    if not process:
        return False

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, MAXIMUM_ALLOWED, ctypes.byref(token)):
        return False

    duplicated = wintypes.HANDLE()
    if not advapi32.DuplicateTokenEx(
        token,
        MAXIMUM_ALLOWED,
        0,
        SECURITY_IMPERSONATION,
        TOKEN_PRIMARY,
        ctypes.byref(duplicated),
    ):
        return False

    base_path = get_base_path()
    cmd_line = _build_agent_command(
        base_path=base_path,
        request_path=request_path,
        decision=decision,
    )
    if not cmd_line:
        log_debug("Cannot launch agent: missing request path or decision.")
        return False

    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(STARTUPINFO)
    startup.lpDesktop = "winsta0\\winlogon"
    proc_info = PROCESS_INFORMATION()

    if advapi32.CreateProcessAsUserW(
        duplicated,
        None,
        cmd_line,
        None,
        None,
        False,
        0,
        None,
        base_path,
        ctypes.byref(startup),
        ctypes.byref(proc_info),
    ):
        log_debug(f"Agent launched. PID={proc_info.dwProcessId}")
        kernel32.CloseHandle(proc_info.hProcess)
        kernel32.CloseHandle(proc_info.hThread)
        return True

    log_debug(f"CreateProcessAsUserW failed: {ctypes.GetLastError()}")
    return False


def dispatch_decision_to_secure_desktop(decision: str, *, session_id: int | None = None) -> bool:
    clean = str(decision or "").strip().upper()
    if clean not in {"ALLOW", "DENY"}:
        log_debug(f"Invalid decision requested: {decision}")
        return False

    if session_id is None:
        session_id = int(kernel32.WTSGetActiveConsoleSessionId())
    if int(session_id) == 0xFFFFFFFF:
        log_debug("No active console session while dispatching UAC decision")
        return False

    return inject_agent_to_winlogon(int(session_id), decision=clean)


class _OrchestratorApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _write_json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(200, {"ok": True, "service": "uac-orchestrator"})
            return
        self._write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/uac/decision":
            self._write_json(404, {"ok": False, "error": "not_found"})
            return

        try:
            raw_length = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            raw_length = 0
        raw_body = self.rfile.read(max(0, raw_length)) if raw_length > 0 else b""
        try:
            payload = json.loads(raw_body.decode("utf-8", errors="replace") or "{}")
        except Exception:
            payload = {}

        decision = str(payload.get("decision") or "").strip().upper()
        if decision not in {"ALLOW", "DENY"}:
            self._write_json(400, {"ok": False, "error": "invalid_decision"})
            return

        ok = dispatch_decision_to_secure_desktop(decision)
        if ok:
            self._write_json(200, {"ok": True, "decision": decision})
            return

        self._write_json(503, {"ok": False, "error": "dispatch_failed", "decision": decision})


def _start_api_server() -> ThreadingHTTPServer | None:
    host = _api_host()
    port = _api_port()
    try:
        server = ThreadingHTTPServer((host, port), _OrchestratorApiHandler)
    except Exception as exc:
        log_debug(f"UAC orchestrator API server failed to start on {host}:{port}: {exc}")
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="UacOrchestratorApi",
        daemon=True,
    )
    thread.start()
    log_debug(f"UAC orchestrator API listening on http://{host}:{port}")
    return server


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--decision")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    log_debug("Orchestrator started")
    enable_privilege("SeDebugPrivilege")
    enable_privilege("SeTcbPrivilege")

    one_shot_decision = str(getattr(args, "decision", "") or "").strip().upper()
    if one_shot_decision:
        ok = dispatch_decision_to_secure_desktop(one_shot_decision)
        log_debug(
            f"One-shot UAC decision dispatch {one_shot_decision}: {'ok' if ok else 'failed'}"
        )
        return

    _start_api_server()

    claimed: dict[str, float] = {}

    while True:
        now = time.time()
        claimed = {
            nonce: claimed_at
            for nonce, claimed_at in claimed.items()
            if now - claimed_at < CLAIM_TTL_SECONDS
        }

        for request_path in pending_request_paths(
            max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS
        ):
            request_payload = load_request(
                request_path,
                max_age_seconds=Config.UAC_REQUEST_MAX_AGE_SECONDS,
            )
            if not request_payload:
                continue

            nonce = str(request_payload.get("nonce") or "")
            if nonce in claimed:
                continue

            session_id = kernel32.WTSGetActiveConsoleSessionId()
            if session_id == 0xFFFFFFFF:
                log_debug("No active console session for UAC request")
                continue

            if inject_agent_to_winlogon(int(session_id), str(request_path)):
                claimed[nonce] = time.time()

        time.sleep(0.5)


if __name__ == "__main__":
    main()
