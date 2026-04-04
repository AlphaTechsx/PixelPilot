from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from install import RUNTIME_ENTRY, RUNTIME_EXE_NAME, compile_script


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the packaged PixelPilot runtime executable.")
    parser.add_argument(
        "--python",
        dest="python_exe",
        default=sys.executable,
        help="Python executable to use for the PyInstaller build.",
    )
    args = parser.parse_args()

    runtime_exe = compile_script(RUNTIME_ENTRY, RUNTIME_EXE_NAME, python_exe=args.python_exe)
    return 0 if runtime_exe else 1


if __name__ == "__main__":
    raise SystemExit(main())
