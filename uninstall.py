import argparse
import ctypes
import os
import shutil
import stat
import subprocess
import sys
import winreg
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

tasks = ["PixelPilotUACOrchestrator", "PixelPilotApp"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin() -> None:
    params = subprocess.list2cmdline(sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def _remove_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
            print(f"[+] Removed file: {path}")
    except Exception as exc:
        print(f"[!] Failed to remove file {path}: {exc}")


def _handle_remove_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _remove_dir(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=False, onerror=_handle_remove_error)
            print(f"[+] Removed directory: {path}")
        else:
            print(f"[i] Directory not found: {path}")
    except Exception as exc:
        print(f"[!] Failed to remove directory {path}: {exc}")


def _remove_task(task_name: str) -> None:
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"[+] Removed scheduled task: {task_name}")
        else:
            msg = (result.stdout or "") + (result.stderr or "")
            if "cannot find" in msg.lower():
                print(f"[i] Task not found: {task_name}")
            else:
                print(f"[!] Failed to remove task {task_name}: {msg.strip()}")
    except Exception as exc:
        print(f"[!] Error removing task {task_name}: {exc}")


def _stop_task(task_name: str) -> None:
    try:
        subprocess.run(["schtasks", "/End", "/TN", task_name], capture_output=True)
    except Exception:
        pass


def _kill_processes() -> None:
    print("[*] Stopping PixelPilot processes...")
    for image_name in [
        "orchestrator.exe",
        "agent.exe",
        "pixelpilot.exe",
        "electron.exe",
        "node.exe",
    ]:
        try:
            subprocess.run(["taskkill", "/F", "/IM", image_name], capture_output=True)
        except Exception:
            pass

    repo_pattern = str(REPO_ROOT).replace("'", "''")
    ps_cmd = (
        f"$pattern = '*{repo_pattern}*'; "
        "Get-Process | "
        "Where-Object { $_.Path -and $_.Path -like $pattern } | "
        "Stop-Process -Force -ErrorAction SilentlyContinue"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
    except Exception:
        pass

    ps_cmd = (
        f"$repo = '{repo_pattern}'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and ($_.Name -in 'wscript.exe','cscript.exe') -and "
        "($_.CommandLine -like ('*' + $repo + '*') -or $_.CommandLine -like '*launch_pixelpilot.vbs*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
    except Exception:
        pass


def _desktop_path() -> Path:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        desktop, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        desktop = os.path.expandvars(desktop)
        return Path(desktop)
    except Exception:
        return Path(os.path.expanduser("~/Desktop"))


def remove_shortcut() -> None:
    shortcut_path = _desktop_path() / "Pixel Pilot.lnk"
    _remove_file(shortcut_path)


def remove_uac_temp_files() -> None:
    temp_root = os.environ.get("SystemRoot", r"C:\Windows")
    temp_dir = Path(temp_root) / "Temp"
    for name in [
        "uac_trigger.txt",
        "uac_snapshot.bmp",
        "uac_response.txt",
        "uac_debug.log",
        "uac_agent_debug.log",
    ]:
        _remove_file(temp_dir / name)
    _remove_dir(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "PixelPilot" / "uac")


def remove_app_index_cache() -> None:
    cache_path = Path.home() / ".pixelpilot" / "app_index.json"
    _remove_file(cache_path)
    try:
        cache_dir = cache_path.parent
        if cache_dir.exists() and not any(cache_dir.iterdir()):
            cache_dir.rmdir()
            print(f"[+] Removed empty cache dir: {cache_dir}")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="PixelPilot Uninstaller")
    parser.add_argument("--no-tasks", action="store_true", help="Skip removing scheduled tasks and shortcut")
    parser.add_argument("--keep-venv", action="store_true", help="Keep venv directory")
    parser.add_argument("--keep-dist", action="store_true", help="Keep dist directory")
    parser.add_argument("--keep-build", action="store_true", help="Keep build directory")
    parser.add_argument("--keep-logs", action="store_true", help="Keep logs directory")
    parser.add_argument("--keep-media", action="store_true", help="Keep media directory")
    parser.add_argument("--keep-cache", action="store_true", help="Keep app index cache")
    args = parser.parse_args()

    print("=== PIXEL PILOT UNINSTALLER ===")

    if not args.no_tasks:
        if not is_admin():
            print("[!] Admin rights required to remove scheduled tasks/shortcut.")
            run_as_admin()
            return

        for task_name in tasks:
            _stop_task(task_name)

        for task_name in tasks:
            _remove_task(task_name)

        remove_shortcut()
        remove_uac_temp_files()

    _kill_processes()

    if not args.keep_venv:
        _remove_dir(REPO_ROOT / "venv")

    if not args.keep_dist:
        _remove_dir(REPO_ROOT / "dist")
        _remove_dir(REPO_ROOT / "desktop" / "dist")
        _remove_dir(REPO_ROOT / "desktop" / "resources" / "runtime")

    if not args.keep_build:
        _remove_dir(REPO_ROOT / "build")
        _remove_dir(REPO_ROOT / "desktop" / "node_modules")

    if not args.keep_logs:
        _remove_dir(REPO_ROOT / "logs")

    if not args.keep_media:
        _remove_dir(REPO_ROOT / "media")

    if not args.keep_cache:
        remove_app_index_cache()

    print("=== UNINSTALL COMPLETE ===")
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
