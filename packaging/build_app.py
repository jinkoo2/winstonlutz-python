"""Build WinstonLutz.gui and WinstonLutz.service one-file binaries with PyInstaller."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

GUI_NAME = "WinstonLutz.gui"
SERVICE_NAME = "WinstonLutz.service"


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


def _pyinstaller_cmd(
    root: Path,
    *,
    name: str,
    entry: Path,
    windowed: bool,
    work: Path,
    dist: Path,
    extra: list[str],
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
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
    ]
    if windowed:
        cmd.append("--windowed")
    else:
        cmd.append("--console")
    cmd.extend(extra)
    cmd.append(str(entry))
    return cmd


def _publish(built: Path, stem: str, version: str, dist: Path) -> Path:
    suffix = exe_suffix()
    canonical = dist / f"{stem}{suffix}"
    if built.resolve() != canonical.resolve():
        if canonical.exists():
            canonical.unlink()
        shutil.move(str(built), str(canonical))
    versioned = dist / f"{stem}-{version}-{platform_tag()}{suffix}"
    if versioned.exists():
        versioned.unlink()
    shutil.copy2(str(canonical), str(versioned))
    print(f"built {canonical}", flush=True)
    print(f"built {versioned}", flush=True)
    return canonical


def _build_one(root: Path, dist: Path, version: str, kind: str) -> Path:
    suffix = exe_suffix()
    if kind == "gui":
        work = root / "build" / "pyinstaller-gui"
        cmd = _pyinstaller_cmd(
            root,
            name=GUI_NAME,
            entry=root / "packaging" / "run_gui.py",
            windowed=True,
            work=work,
            dist=dist,
            extra=[
                "--hidden-import",
                "PyQt5.sip",
                "--hidden-import",
                "PyQt5.QtCore",
                "--hidden-import",
                "PyQt5.QtGui",
                "--hidden-import",
                "PyQt5.QtWidgets",
            ],
        )
        built = dist / f"{GUI_NAME}{suffix}"
        stem = GUI_NAME
    else:
        work = root / "build" / "pyinstaller-service"
        cmd = _pyinstaller_cmd(
            root,
            name=SERVICE_NAME,
            entry=root / "packaging" / "run_service.py",
            windowed=False,
            work=work,
            dist=dist,
            extra=[
                "--hidden-import",
                "watchdog",
                "--hidden-import",
                "watchdog.observers",
                "--hidden-import",
                "watchdog.events",
                "--hidden-import",
                "winstonlutz.watcher",
                "--exclude-module",
                "PyQt5",
                "--exclude-module",
                "PyQt5.sip",
            ],
        )
        built = dist / f"{SERVICE_NAME}{suffix}"
        stem = SERVICE_NAME
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=root)
    if not built.is_file():
        print(f"expected binary not found: {built}", file=sys.stderr)
        raise SystemExit(1)
    return _publish(built, stem, version, dist)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WinstonLutz.gui and WinstonLutz.service binaries")
    parser.add_argument("--version", default=os.environ.get("APP_VERSION", "").strip())
    parser.add_argument(
        "--only",
        choices=("all", "gui", "service"),
        default="all",
        help="build both (default), or one target",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = args.version or project_version(root)
    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    kinds = ("gui", "service") if args.only == "all" else (args.only,)
    for kind in kinds:
        _build_one(root, dist, version, kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
