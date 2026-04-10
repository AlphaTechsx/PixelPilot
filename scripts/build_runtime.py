from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
SRC_DIR = REPO_ROOT / "src"


@dataclass(frozen=True)
class BuildTarget:
    script_path: Path
    name: str

    @property
    def exe_name(self) -> str:
        return f"{self.name}.exe"

    @property
    def output_dir(self) -> Path:
        return DIST_DIR / self.name

    @property
    def output_executable(self) -> Path:
        return self.output_dir / self.exe_name


BUILD_TARGETS = (
    BuildTarget(REPO_ROOT / "src" / "runtime" / "bootstrap.py", "pixelpilot-runtime"),
    BuildTarget(REPO_ROOT / "src" / "uac" / "orchestrator.py", "orchestrator"),
    BuildTarget(REPO_ROOT / "src" / "uac" / "agent.py", "agent"),
)


def _kill_process(image_name: str) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/IM", image_name], capture_output=True, check=False)
    except Exception:
        pass


def _ensure_pyinstaller(python_exe: str) -> None:
    try:
        subprocess.run(
            [python_exe, "-m", "PyInstaller", "--version"],
            capture_output=True,
            check=True,
        )
    except Exception:
        print("[*] Installing PyInstaller into the selected Python environment...")
        subprocess.run([python_exe, "-m", "pip", "install", "pyinstaller"], check=True)


def build_target(target: BuildTarget, python_exe: str) -> Path | None:
    print(f"[*] Compiling {target.script_path}...")

    _kill_process(target.exe_name)
    if target.exe_name != "orchestrator.exe":
        _kill_process("orchestrator.exe")

    if target.output_dir.exists():
        shutil.rmtree(target.output_dir, ignore_errors=True)
    legacy_output = DIST_DIR / target.exe_name
    if legacy_output.exists():
        legacy_output.unlink(missing_ok=True)

    _ensure_pyinstaller(python_exe)

    command = [
        python_exe,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconsole",
        "--collect-data",
        "torchfree_ocr",
        "--paths",
        str(SRC_DIR),
        "--distpath",
        str(DIST_DIR),
        "--specpath",
        str(DIST_DIR),
        "--name",
        target.name,
        str(target.script_path),
    ]

    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"[-] Compilation failed for {target.script_path} (exit code {result.returncode}).")
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if stderr:
            print(stderr)
        elif stdout:
            print(stdout)
        return None

    output_path = target.output_executable
    if not output_path.exists():
        print(f"[-] Expected runtime binary directory was not created: {output_path}")
        return None

    print(f"[+] Compiled successfully: {output_path}")
    return output_path


def default_python() -> str:
    venv_python = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the packaged PixelPilot runtime binaries.")
    parser.add_argument(
        "--python",
        dest="python_exe",
        default=default_python(),
        help="Python executable to use for the PyInstaller build.",
    )
    args = parser.parse_args()

    python_exe = shutil.which(args.python_exe) or args.python_exe
    if not Path(python_exe).exists() and python_exe != args.python_exe:
        python_exe = args.python_exe

    failed = []
    for target in BUILD_TARGETS:
        if build_target(target, python_exe) is None:
            failed.append(target.exe_name)

    if failed:
        print(f"[-] Failed to build packaged runtime binaries: {', '.join(failed)}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
