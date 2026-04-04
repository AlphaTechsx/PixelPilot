from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = REPO_ROOT / "desktop"
PACKAGED_EXE_CANDIDATES = (
    DESKTOP_DIR / "dist" / "win-unpacked" / "PixelPilot.exe",
    DESKTOP_DIR / "dist" / "PixelPilot.exe",
)
DEV_BUILD_ENTRY = DESKTOP_DIR / "dist" / "main" / "index.js"


def _find_npm() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def _run(command: list[str], *, cwd: Path) -> int:
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    return int(subprocess.run(command, cwd=str(cwd), env=env, check=False).returncode)


def _resolve_packaged_exe() -> Path | None:
    explicit = os.environ.get("PIXELPILOT_DESKTOP_EXE", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.exists():
            return path
    for candidate in PACKAGED_EXE_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    packaged_exe = _resolve_packaged_exe()
    if packaged_exe is not None:
        return _run([str(packaged_exe)], cwd=packaged_exe.parent)

    if not DESKTOP_DIR.exists():
        print(f"Desktop shell directory not found: {DESKTOP_DIR}", file=sys.stderr)
        return 1

    npm = _find_npm()
    if npm is None:
        print(
            "npm was not found. Install Node.js so PixelPilot can build and launch the desktop shell.",
            file=sys.stderr,
        )
        return 1

    if not (DESKTOP_DIR / "node_modules").exists():
        install_code = _run([npm, "install"], cwd=DESKTOP_DIR)
        if install_code != 0:
            return install_code

    if not DEV_BUILD_ENTRY.exists():
        build_code = _run([npm, "run", "build"], cwd=DESKTOP_DIR)
        if build_code != 0:
            return build_code

    return _run([npm, "start"], cwd=DESKTOP_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
