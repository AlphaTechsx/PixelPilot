from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from config import Config


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class DoctorReport:
    status: str
    checks: list[DoctorCheck]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [item.as_dict() for item in self.checks],
        }

    def render_text(self) -> str:
        lines = [f"PixelPilot doctor: {self.status.upper()}"]
        for check in self.checks:
            lines.append(f"[{check.status.upper()}] {check.name}: {check.summary}")
        return "\n".join(lines)


def run_doctor(
    *,
    agent: Any = None,
    controller: Any = None,
    runtime_service: Any = None,
) -> DoctorReport:
    extension_manager = (
        getattr(agent, "extension_manager", None)
        or getattr(getattr(controller, "agent", None), "extension_manager", None)
    )
    checks = [
        _check_direct_mode(),
        _check_backend(),
        _check_wakeword_assets(),
        _check_audio_devices(),
        _check_uac_tasks(),
        _check_app_startup(),
        _check_bridge(runtime_service=runtime_service),
        _check_app_index(agent=agent or getattr(controller, "agent", None)),
        _check_extensions(extension_manager),
    ]
    statuses = [item.status for item in checks]
    overall = "error" if "error" in statuses else ("warn" if "warn" in statuses else "ok")
    return DoctorReport(status=overall, checks=checks)


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    report = run_doctor()
    if "--json" in args:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(report.render_text())
    return 0 if report.status != "error" else 1


def _check_direct_mode() -> DoctorCheck:
    enabled = bool(Config.USE_DIRECT_API)
    has_key = bool(str(Config.GEMINI_API_KEY or "").strip())
    if enabled and has_key:
        return DoctorCheck(
            name="direct_mode",
            status="ok",
            summary="Direct API mode is configured.",
            details={"enabled": True},
        )
    if enabled and not has_key:
        return DoctorCheck(
            name="direct_mode",
            status="error",
            summary="Direct API mode is enabled but GEMINI_API_KEY is missing.",
            details={"enabled": True},
        )
    return DoctorCheck(
        name="direct_mode",
        status="warn",
        summary="Direct API mode is disabled; PixelPilot will rely on backend mode.",
        details={"enabled": False},
    )


def _check_backend() -> DoctorCheck:
    base_url = str(Config.BACKEND_URL or "").strip()
    if not base_url:
        return DoctorCheck(
            name="backend",
            status="warn",
            summary="No backend URL is configured.",
        )

    target = base_url.rstrip("/") + "/health"
    try:
        with urllib_request.urlopen(target, timeout=5.0) as response:
            code = int(getattr(response, "status", 200))
        status = "ok" if 200 <= code < 400 else "warn"
        return DoctorCheck(
            name="backend",
            status=status,
            summary=f"Backend health endpoint responded with HTTP {code}.",
            details={"url": target, "statusCode": code},
        )
    except urllib_error.HTTPError as exc:
        status = "warn" if exc.code < 500 else "error"
        return DoctorCheck(
            name="backend",
            status=status,
            summary=f"Backend health endpoint returned HTTP {exc.code}.",
            details={"url": target, "statusCode": exc.code},
        )
    except Exception as exc:
        return DoctorCheck(
            name="backend",
            status="error",
            summary=f"Backend health check failed: {exc}",
            details={"url": target},
        )


def _check_wakeword_assets() -> DoctorCheck:
    if not bool(Config.ENABLE_WAKE_WORD):
        return DoctorCheck(
            name="wakeword_assets",
            status="warn",
            summary="Wake word is disabled by configuration.",
        )

    model_path = Config.resolve_wake_word_openwakeword_model_path()
    feature_path, embedding_path = Config.resolve_wake_word_openwakeword_feature_model_paths(
        model_path=model_path
    )
    exists = all(
        path is not None and Path(path).exists()
        for path in (model_path, feature_path, embedding_path)
    )
    return DoctorCheck(
        name="wakeword_assets",
        status="ok" if exists else "error",
        summary=(
            "Wake-word model assets are available."
            if exists
            else "Wake-word model assets are missing."
        ),
        details={
            "modelPath": str(model_path or ""),
            "featureModelPath": str(feature_path or ""),
            "embeddingModelPath": str(embedding_path or ""),
        },
    )


def _check_audio_devices() -> DoctorCheck:
    try:
        import pyaudio
    except Exception as exc:
        return DoctorCheck(
            name="audio_devices",
            status="error",
            summary=f"PyAudio is unavailable: {exc}",
        )

    audio = None
    try:
        audio = pyaudio.PyAudio()
        device_count = int(audio.get_device_count())
        inputs = 0
        outputs = 0
        for index in range(device_count):
            info = audio.get_device_info_by_index(index)
            if int(info.get("maxInputChannels") or 0) > 0:
                inputs += 1
            if int(info.get("maxOutputChannels") or 0) > 0:
                outputs += 1
        status = "ok" if inputs > 0 and outputs > 0 else "warn"
        return DoctorCheck(
            name="audio_devices",
            status=status,
            summary=f"Found {inputs} input and {outputs} output audio devices.",
            details={
                "deviceCount": device_count,
                "inputDevices": inputs,
                "outputDevices": outputs,
            },
        )
    except Exception as exc:
        return DoctorCheck(
            name="audio_devices",
            status="error",
            summary=f"Audio device probe failed: {exc}",
        )
    finally:
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass


def _check_uac_tasks() -> DoctorCheck:
    if shutil.which("schtasks") is None:
        return DoctorCheck(
            name="uac_tasks",
            status="warn",
            summary="schtasks is unavailable, so scheduled tasks could not be verified.",
        )

    task_names = ["PixelPilot Orchestrator", "PixelPilot UAC Agent"]
    missing = []
    for task_name in task_names:
        completed = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if int(completed.returncode) != 0:
            missing.append(task_name)
    if missing:
        return DoctorCheck(
            name="uac_tasks",
            status="warn",
            summary="Some scheduled tasks are missing.",
            details={"missing": missing},
        )
    return DoctorCheck(
        name="uac_tasks",
        status="ok",
        summary="Required UAC scheduled tasks are registered.",
        details={"tasks": task_names},
    )


def _check_app_startup() -> DoctorCheck:
    if sys.platform != "win32":
        return DoctorCheck(
            name="app_startup",
            status="warn",
            summary="Startup entry verification is only available on Windows.",
        )

    try:
        import winreg
    except Exception as exc:
        return DoctorCheck(
            name="app_startup",
            status="warn",
            summary=f"Windows registry access is unavailable: {exc}",
        )

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "PixelPilot"
    checked_roots: list[str] = []

    for root_name, root in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
        checked_roots.append(root_name)
        try:
            with winreg.OpenKey(root, key_path) as handle:
                value, _ = winreg.QueryValueEx(handle, value_name)
        except FileNotFoundError:
            continue
        except OSError:
            continue

        command = str(value or "").strip()
        has_background_arg = "--background-startup" in command.lower()
        return DoctorCheck(
            name="app_startup",
            status="ok" if has_background_arg else "warn",
            summary=(
                "PixelPilot is registered to start in the background at user sign-in."
                if has_background_arg
                else "PixelPilot has a startup entry, but it is missing the background startup flag."
            ),
            details={
                "root": root_name,
                "command": command,
                "valueName": value_name,
            },
        )

    return DoctorCheck(
        name="app_startup",
        status="warn",
        summary="PixelPilot is not registered to start automatically at sign-in.",
        details={
            "checkedRoots": checked_roots,
            "valueName": value_name,
        },
    )


def _check_bridge(*, runtime_service: Any = None) -> DoctorCheck:
    from runtime.service import ElectronRuntimeService

    host, port, token = ElectronRuntimeService.resolve_bridge_settings()
    details = {
        "host": host,
        "port": port,
        "tokenConfigured": bool(token),
    }
    if runtime_service is None:
        return DoctorCheck(
            name="runtime_bridge",
            status="warn",
            summary="Runtime bridge settings resolved, but no live bridge instance was provided.",
            details=details,
        )

    bridge_server = getattr(runtime_service, "bridge_server", None)
    ready = bool(getattr(bridge_server, "_runtime_ready", False))
    if ready:
        return DoctorCheck(
            name="runtime_bridge",
            status="ok",
            summary="Runtime bridge is online.",
            details=details,
        )
    return DoctorCheck(
        name="runtime_bridge",
        status="warn",
        summary="Runtime bridge settings resolved, but the bridge is not marked ready.",
        details=details,
    )


def _check_app_index(*, agent: Any = None) -> DoctorCheck:
    service = getattr(agent, "app_indexer", None) if agent is not None else None
    if service is not None:
        state = str(getattr(service, "state", "idle") or "idle")
        app_count = int(getattr(service, "app_count", 0) or 0)
        status = "ok" if state == "ready" else ("warn" if state == "loading" else "error")
        summary = (
            f"App index is ready with {app_count} apps."
            if state == "ready"
            else f"App index state is {state}."
        )
        return DoctorCheck(
            name="app_index",
            status=status,
            summary=summary,
            details={
                "state": state,
                "appCount": app_count,
                "cachePath": str(Config.APP_INDEX_PATH),
                "error": str(getattr(service, "error", "") or ""),
            },
        )

    cache_path = Path(Config.APP_INDEX_PATH).expanduser()
    exists = cache_path.exists()
    return DoctorCheck(
        name="app_index",
        status="ok" if exists else "warn",
        summary="App index cache exists." if exists else "App index cache does not exist yet.",
        details={"cachePath": str(cache_path)},
    )


def _check_extensions(extension_manager: Any) -> DoctorCheck:
    summary = {}
    if extension_manager is not None and hasattr(extension_manager, "summary"):
        try:
            summary = dict(extension_manager.summary())
        except Exception:
            summary = {}
    plugins = int(summary.get("pluginCount", 0) or 0)
    mcp_servers = int(summary.get("mcpServerCount", 0) or 0)
    tools = int(summary.get("toolCount", 0) or 0)
    if not summary:
        return DoctorCheck(
            name="extensions",
            status="ok",
            summary="No extension manager was active.",
        )
    return DoctorCheck(
        name="extensions",
        status="ok",
        summary=f"Loaded {plugins} plugins, {mcp_servers} MCP servers, and {tools} extension tools.",
        details=summary,
    )
