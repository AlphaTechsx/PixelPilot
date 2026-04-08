import os
import sys
import subprocess
import ctypes
import tempfile
import winreg
import argparse
import venv
import time
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

ORCHESTRATOR_SCRIPT = REPO_ROOT / "src" / "uac" / "orchestrator.py"
ORCHESTRATOR_EXE_NAME = "orchestrator.exe"
ORCHESTRATOR_TASK_NAME = "PixelPilotUACOrchestrator"

AGENT_SCRIPT = REPO_ROOT / "src" / "uac" / "agent.py"
AGENT_EXE_NAME = "agent.exe"

MAIN_APP_SCRIPT = REPO_ROOT / "src" / "main.py"
MAIN_APP_TASK_NAME = "PixelPilotApp"
DESKTOP_DIR = REPO_ROOT / "desktop"
RUNTIME_ENTRY = REPO_ROOT / "src" / "runtime" / "bootstrap.py"
RUNTIME_EXE_NAME = "pixelpilot-runtime.exe"

DEFAULT_VENV_DIR = REPO_ROOT / "venv"
DEFAULT_REQUIREMENTS = REPO_ROOT / "requirements.txt"
UAC_IPC_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "PixelPilot" / "uac"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin(extra_args: list[str] | None = None) -> bool:
    argv = list(sys.argv)
    if extra_args:
        argv.extend(extra_args)
    params = subprocess.list2cmdline(argv)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return int(result) > 32


def ensure_venv(venv_dir: Path = DEFAULT_VENV_DIR) -> str | None:
    """Create a venv if it does not exist and return its python executable path.""" 
    venv_dir = Path(venv_dir)
    python_exe = venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )

    if python_exe.exists():
        print(f"[+] Virtualenv already present at {venv_dir}")
        return str(python_exe)

    print(f"[*] Creating virtualenv at {venv_dir}...")
    try:
        venv.create(str(venv_dir), with_pip=True)
        time.sleep(0.5)
    except Exception as exc:
        print(f"[-] Failed to create virtualenv: {exc}")
        return None

    if python_exe.exists():
        print(f"[+] Virtualenv created: {python_exe}")
        return str(python_exe)

    print(f"[-] Failed to create virtualenv at {venv_dir}")
    return None


def install_requirements(python_exe: str, requirements_file: Path = DEFAULT_REQUIREMENTS) -> bool:
    """Install requirements into the given python executable (venv)."""
    if not python_exe:
        print("[-] No Python executable provided for installing requirements.")
        return False

    requirements_file = Path(requirements_file)
    if not requirements_file.exists():
        print(f"[!] Requirements file '{requirements_file}' not found, skipping pip install.")
        return True

    print(f"[*] Upgrading pip and installing from {requirements_file}...")
    try:
        subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([python_exe, "-m", "pip", "install", "-r", str(requirements_file)], check=True)
        print("[+] Requirements installed into venv.")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[-] Failed to install requirements: {exc}")
        return False


def prefetch_ocr_models(python_exe: str) -> bool:
    """Download EasyOCR models during install to avoid first-run delay."""
    if not python_exe:
        print("[-] No Python executable provided for OCR prefetch.")
        return False

    cmd = [
        python_exe,
        "-c",
        "import easyocr; easyocr.Reader(['en'], gpu=False)",
    ]
    try:
        print("[*] Prefetching EasyOCR models...")
        subprocess.run(cmd, check=True)
        print("[+] EasyOCR models downloaded.")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[!] EasyOCR prefetch failed: {exc}")
        return False


def prebuild_app_index(python_exe: str) -> bool:
    """Build the app index cache during install to avoid first-run delay."""
    if not python_exe:
        print("[-] No Python executable provided for app index prebuild.")
        return False

    repo_root = str(REPO_ROOT)
    cmd = [
        python_exe,
        "-c",
        (
            "import sys; "
            f"sys.path.insert(0, r'{repo_root}\\src'); "
            "from tools.app_indexer import AppIndexer; "
            "AppIndexer()"
        ),
    ]

    try:
        print("[*] Building app index cache...")
        subprocess.run(cmd, check=True)
        print("[+] App index cache built.")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[!] App index prebuild failed: {exc}")
        return False


def _find_npm() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def prepare_desktop_shell(python_exe: str | None = None) -> bool:
    if not DESKTOP_DIR.exists():
        print(f"[-] Desktop shell directory not found: {DESKTOP_DIR}")
        return False

    npm = _find_npm()
    if not npm:
        print("[-] npm was not found. Install Node.js to build the desktop shell.")
        return False

    try:
        print("[*] Compiling packaged desktop runtime...")
        runtime_exe = compile_script(RUNTIME_ENTRY, RUNTIME_EXE_NAME, python_exe=python_exe)
        if not runtime_exe:
            print("[-] Failed to build the packaged desktop runtime.")
            return False
        print("[*] Installing desktop shell dependencies...")
        subprocess.run([npm, "install"], cwd=str(DESKTOP_DIR), check=True)
        print("[*] Building desktop shell...")
        subprocess.run([npm, "run", "build"], cwd=str(DESKTOP_DIR), check=True)
        print("[+] Desktop shell is ready.")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[-] Failed to prepare the desktop shell: {exc}")
        return False


def _try_kill_image(image_name: str) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/IM", image_name], capture_output=True)
    except Exception:
        pass


def compile_script(script_path: Path, exe_name: str, python_exe: str | None = None) -> str | None:
    print(f"[*] Compiling {script_path}...")

    for proc in [exe_name, "uac_orchestrator.exe", "orchestrator.exe"]:
        _try_kill_image(proc)

    python_cmd = python_exe or sys.executable

    try:
        subprocess.run([python_cmd, "-m", "PyInstaller", "--version"], capture_output=True, check=True)
    except Exception:
        print("[-] PyInstaller not available in target Python. Installing into that environment...")
        subprocess.run([python_cmd, "-m", "pip", "install", "pyinstaller"], check=True)

    dist_dir = REPO_ROOT / "dist"
    src_dir = REPO_ROOT / "src"
    cmd = [
        python_cmd,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--paths",
        str(src_dir),
        "--distpath",
        str(dist_dir),
        "--specpath",
        str(dist_dir),
        "--name",
        exe_name.replace(".exe", ""),
        str(script_path),
    ]

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[-] Compilation failed for {script_path} (exit code {result.returncode}).")
        stderr = str(result.stderr or "").strip()
        stdout = str(result.stdout or "").strip()
        if stderr:
            print(stderr)
        elif stdout:
            print(stdout)
        return None

    exe_path = dist_dir / exe_name
    if exe_path.exists():
        print(f"[+] Compiled successfully: {exe_path}")
        return str(exe_path)

    print(f"[-] Compilation failed for {script_path}.")
    return None


def ensure_uac_ipc_dir() -> None:
    try:
        UAC_IPC_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[+] UAC IPC directory ready: {UAC_IPC_DIR}")
    except Exception as exc:
        print(f"[!] Failed to prepare UAC IPC directory: {exc}")


def relax_power_settings(task_name: str) -> None:
    """Allow the task to run on battery power."""

    print(f"[*] Relaxing power settings for task: {task_name}")
    ps_cmd = (
        f"$s = Get-ScheduledTask -TaskName '{task_name}'; "
        "if ($s) { "
        "$s.Settings.DisallowStartIfOnBatteries = $false; "
        "$s.Settings.StopIfGoingOnBatteries = $false; "
        f"Set-ScheduledTask -TaskName '{task_name}' -Settings $s.Settings "
        "}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            check=True,
            capture_output=True
        )
        print(f"[+] Power settings relaxed for {task_name}.")
    except subprocess.CalledProcessError as e:
        print(f"[-] Failed to relax power settings for {task_name}: {e}")


def create_system_task(exe_path: str) -> None:
    print(f"[*] Creating Service Task: {ORCHESTRATOR_TASK_NAME}")
    cmd = [
        "schtasks",
        "/Create",
        "/TN",
        ORCHESTRATOR_TASK_NAME,
        "/TR",
        f'"{exe_path}"',
        "/SC",
        "ONSTART",
        "/RU",
        "SYSTEM",
        "/RL",
        "HIGHEST",
        "/F",
    ]
    subprocess.run(cmd, capture_output=True)
    print("[+] Service Task Created. Starting it now...")
    
    relax_power_settings(ORCHESTRATOR_TASK_NAME)
    
    subprocess.run(["schtasks", "/Run", "/TN", ORCHESTRATOR_TASK_NAME], capture_output=True)


def _pythonw_for(python_exe: str) -> str:
    try:
        candidate = os.path.join(os.path.dirname(os.path.abspath(python_exe)), "pythonw.exe")
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    return python_exe


def create_launcher_task(script_path: Path, python_exe: str | None = None, log_path: Path | None = None) -> None:
    print(f"[*] Creating Launcher Task: {MAIN_APP_TASK_NAME}")

    script_path = Path(script_path).resolve()
    if not script_path.exists():
        print(f"[-] Launcher script not found: {script_path}")
        return

    python_exe = os.path.abspath(python_exe or sys.executable)
    if not os.path.exists(python_exe):
        print(f"[!] Python executable not found: {python_exe}. Falling back to current Python.")
        python_exe = os.path.abspath(sys.executable)

    work_dir = str(script_path.parent)

    log_path_str = None
    if log_path:
        log_path = Path(log_path).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path_str = str(log_path)

    launcher_vbs = REPO_ROOT / "logs" / "launch_pixelpilot.vbs"
    launcher_vbs.parent.mkdir(parents=True, exist_ok=True)

    py_esc = python_exe.replace('"', '""')
    script_esc = str(script_path).replace('"', '""')

    if log_path_str:
        log_esc = log_path_str.replace('"', '""')
        run_cmd = f'cmd /c "" ""{py_esc}"" ""{script_esc}"" >> ""{log_esc}"" 2>&1 ""'
    else:
        run_cmd = f'""{py_esc}"" ""{script_esc}""'

    work_dir_esc = work_dir.replace('"', '""')

    vbs_content = (
        f'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.CurrentDirectory = "{work_dir_esc}"\r\n'
        f'WshShell.Run "{run_cmd}", 0, False\r\n'
    )

    launcher_vbs.write_text(vbs_content, encoding="utf-8")

    tr = f'wscript.exe "{launcher_vbs}"'

    cmd = [
        "schtasks",
        "/Create",
        "/TN",
        MAIN_APP_TASK_NAME,
        "/TR",
        tr,
        "/SC",
        "ONCE",
        "/ST",
        "00:00",
        "/RL",
        "HIGHEST",
        "/IT",
        "/F",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[-] Failed to create launcher task: {result.stdout} {result.stderr}")
    else:
        print("[+] Launcher Task Created.")
        relax_power_settings(MAIN_APP_TASK_NAME)


def create_desktop_shortcut() -> None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        desktop, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        desktop = os.path.expandvars(desktop)
    except Exception:
        desktop = os.path.normpath(os.path.expanduser("~/Desktop"))

    shortcut_path = os.path.join(desktop, "Pixel Pilot.lnk")
    print(f"[*] Creating Shortcut: {shortcut_path}")

    ico = REPO_ROOT / "src" / "logos" / "pixelpilot-icon.ico"

    icon_location = sys.executable
    if ico.exists():
        icon_location = str(ico)

    def _escape_ps_single_quotes(value: str) -> str:
        return value.replace("'", "''")

    try:
        ps_shortcut_path = _escape_ps_single_quotes(os.path.abspath(shortcut_path))
        ps_icon_location = _escape_ps_single_quotes(os.path.abspath(icon_location))
        ps_args = _escape_ps_single_quotes(f'/RUN /TN "{MAIN_APP_TASK_NAME}"')

        ps_content = (
            "$ErrorActionPreference = 'Stop'\n"
            "$wsh = New-Object -ComObject WScript.Shell\n"
            f"$s = $wsh.CreateShortcut('{ps_shortcut_path}')\n"
            "$s.TargetPath = 'C:\\Windows\\System32\\schtasks.exe'\n"
            f"$s.Arguments = '{ps_args}'\n"
            f"$s.IconLocation = '{ps_icon_location}, 0'\n"
            "$s.WindowStyle = 7\n"
            "$s.Save()\n"
        )

        fd, ps1_path = tempfile.mkstemp(suffix=".ps1")
        os.close(fd)
        Path(ps1_path).write_text(ps_content, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    ps1_path,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("[+] Shortcut placed on Desktop.")
                return
            print(
                "[-] PowerShell shortcut creation failed; falling back to VBScript. "
                f"(rc={result.returncode}) {result.stdout} {result.stderr}"
            )
        finally:
            try:
                os.remove(ps1_path)
            except Exception:
                pass
    except Exception as exc:
        print(f"[-] PowerShell shortcut creation error; falling back to VBScript: {exc}")

    icon_location = os.path.abspath(icon_location).replace("\\", "\\\\")
    shortcut_path_escaped = shortcut_path.replace("\\", "\\\\")

    vbs_content = fr'''
    Set oWS = WScript.CreateObject("WScript.Shell")
    sLinkFile = "{shortcut_path_escaped}"
    Set oFS = CreateObject("Scripting.FileSystemObject")
    On Error Resume Next
    If oFS.FileExists(sLinkFile) Then oFS.DeleteFile(sLinkFile), True
    Set oLink = oWS.CreateShortcut(sLinkFile)
    oLink.TargetPath = "C:\\Windows\\System32\\schtasks.exe"
    oLink.Arguments = "/RUN /TN \"{MAIN_APP_TASK_NAME}\""
    oLink.IconLocation = "{icon_location}, 0"
    oLink.WindowStyle = 7
    oLink.Save
    '''

    fd, vbs_path = tempfile.mkstemp(suffix=".vbs")
    os.close(fd)
    Path(vbs_path).write_text(vbs_content, encoding="utf-8")

    try:
        result = subprocess.run(["cscript", "//Nologo", vbs_path], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[-] VBScript Error (rc={result.returncode}): {result.stdout} {result.stderr}")
        else:
            print("[+] Shortcut placed on Desktop.")
    finally:
        try:
            os.remove(vbs_path)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Pixel Pilot Installer (desktop shell)")
    parser.add_argument("--no-venv", action="store_true", help="Do not create venv or install requirements")
    parser.add_argument("--venv-dir", default=str(DEFAULT_VENV_DIR), help="Virtualenv directory")
    parser.add_argument(
        "--requirements",
        default=str(DEFAULT_REQUIREMENTS),
        help="Requirements file to install into venv",
    )
    parser.add_argument(
        "--no-tasks",
        action="store_true",
        help="Skip creating scheduled tasks and desktop shortcut",
    )
    parser.add_argument(
        "--resume-phase",
        choices=("full", "admin"),
        default="full",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--python-exe",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    print("=== PIXEL PILOT INSTALLER ===")
    if args.resume_phase == "admin":
        print("[*] Resuming installer at admin-only phase...")

    venv_python: str | None = args.python_exe or None
    if args.resume_phase != "admin":
        if not args.no_venv:
            venv_python = ensure_venv(Path(args.venv_dir))
            if not venv_python:
                print("[-] Virtualenv creation failed. Aborting.")
                return
            if not install_requirements(venv_python, Path(args.requirements)):
                return
            prefetch_ocr_models(venv_python)
            prebuild_app_index(venv_python)
        else:
            venv_python = None

        if not prepare_desktop_shell(venv_python):
            return

    if args.no_tasks:
        print("[*] Skipping scheduled tasks/shortcut (requested).")
        print("=== INSTALL COMPLETE ===")
        return

    if not is_admin():
        print("[!] Admin rights required to set up scheduled tasks/shortcut.")
        elevate_args = ["--resume-phase", "admin"]
        if venv_python:
            elevate_args.extend(["--python-exe", venv_python])
        if not run_as_admin(elevate_args):
            print("[-] Elevation request was cancelled or failed.")
        return

    if not compile_script(AGENT_SCRIPT, AGENT_EXE_NAME, python_exe=venv_python):
        return

    orchestrator_exe = compile_script(ORCHESTRATOR_SCRIPT, ORCHESTRATOR_EXE_NAME, python_exe=venv_python)
    if not orchestrator_exe:
        return

    ensure_uac_ipc_dir()
    create_system_task(orchestrator_exe)

    log_path = REPO_ROOT / "logs" / "app_launch.log"
    create_launcher_task(MAIN_APP_SCRIPT, python_exe=venv_python, log_path=log_path)

    create_desktop_shortcut()

    print("\n=== INSTALLATION COMPLETE ===")
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
