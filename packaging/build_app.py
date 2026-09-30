"""Build a one-file WinstonLutz binary with PyInstaller."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "WinstonLutz"


def project_version(root: Path) -> str:
    for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("version"):
            _, _, value = text.partition("=")
            return value.strip().strip('"').strip("'")
    return "0.0.0"


def platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        system = "macos"
    if machine in ("amd64", "x86_64"):
        machine = "x64"
    elif machine in ("aarch64", "arm64"):
        machine = "arm64"
    return f"{system}-{machine}"


def exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WinstonLutz binary")
    parser.add_argument("--version", default=os.environ.get("APP_VERSION", "").strip())
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = args.version or project_version(root)
    dist = root / "dist"
    work = root / "build" / "pyinstaller"
    dist.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--paths",
        str(root),
        "--workpath",
        str(work),
        "--distpath",
        str(dist),
        "--specpath",
        str(root / "packaging"),
        "--collect-all",
        "SimpleITK",
        "--collect-all",
        "pydicom",
        "--collect-data",
        "winstonlutz",
        "--hidden-import",
        "watchdog",
        "--hidden-import",
        "watchdog.observers",
        "--hidden-import",
        "watchdog.events",
        "--hidden-import",
        "winstonlutz.watcher",
        "--hidden-import",
        "PyQt5.sip",
        "--hidden-import",
        "PyQt5.QtCore",
        "--hidden-import",
        "PyQt5.QtGui",
        "--hidden-import",
        "PyQt5.QtWidgets",
        str(root / "packaging" / "run_gui.py"),
    ]
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=root)

    suffix = exe_suffix()
    built = dist / f"{APP_NAME}{suffix}"
    if not built.is_file():
        print(f"expected binary not found: {built}", file=sys.stderr)
        return 1
    versioned = dist / f"{APP_NAME}-{version}-{platform_tag()}{suffix}"
    if versioned.exists():
        versioned.unlink()
    shutil.copy2(str(built), str(versioned))
    print(f"built {built}", flush=True)
    print(f"built {versioned}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
