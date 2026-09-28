"""Build a one-file Winston-Lutz GUI with PyInstaller."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WinstonLutz GUI binary")
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
        "WinstonLutz",
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

    built = dist / ("WinstonLutz.exe" if os.name == "nt" else "WinstonLutz")
    if not built.is_file():
        print(f"expected binary not found: {built}", file=sys.stderr)
        return 1
    suffix = ".exe" if os.name == "nt" else ""
    named = dist / f"WinstonLutz-{version}-{platform_tag()}{suffix}"
    if named.exists():
        named.unlink()
    shutil.move(str(built), str(named))
    print(f"built {named}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
