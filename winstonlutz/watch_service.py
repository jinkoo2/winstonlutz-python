"""Install Winston-Lutz watch as a Windows service via NSSM."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .app_settings import SETTINGS_NAME, app_dir, user_config_path

DEFAULT_SERVICE_NAME = "WinstonLutzWatch"
DEFAULT_DISPLAY_NAME = "Winston-Lutz Watch"
APP_EXE_STEM = "WinstonLutz"
SOURCE_APP_PARAMETERS = "-u -m winstonlutz watch"
FROZEN_APP_PARAMETERS = "--mode service"
APP_PARAMETERS = SOURCE_APP_PARAMETERS


@dataclass
class WatchServicePlan:
    service_name: str = DEFAULT_SERVICE_NAME
    display_name: str = DEFAULT_DISPLAY_NAME
    nssm_exe: str = ""
    program_exe: str = ""
    app_parameters: str = ""
    app_directory: str = ""
    settings_file: str = ""
    account: str = ""
    password: str = ""
    start_after: bool = True
    replace_existing: bool = True


def is_windows() -> bool:
    return sys.platform == "win32"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def program_basename(program_exe: str) -> str:
    """File name of *program_exe*, including Windows paths on POSIX Python."""
    text = str(program_exe or "").replace("\\", "/").rstrip("/")
    return Path(text).name.lower()


def is_packaged_exe(program_exe: str) -> bool:
    name = program_basename(program_exe)
    return name.startswith("winstonlutz")


def default_app_parameters(program_exe: str = "") -> str:
    name = program_basename(program_exe)
    if name.startswith("python"):
        return SOURCE_APP_PARAMETERS
    if is_frozen() or is_packaged_exe(program_exe):
        return FROZEN_APP_PARAMETERS
    return SOURCE_APP_PARAMETERS


def find_packaged_exe(folder: Path | None = None) -> str:
    """WinstonLutz next to the running app, with or without ``.exe``.

    Also accepts versioned release names such as
    ``WinstonLutz-0.5.0-windows-x64.exe``.
    """
    folder = folder or app_dir()
    suffix = _exe_suffix()
    exact = [
        folder / f"{APP_EXE_STEM}{suffix}",
        folder / APP_EXE_STEM,
        folder / f"{APP_EXE_STEM}.exe",
    ]
    seen: set[Path] = set()
    for path in exact:
        resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return str(resolved)
    matches = [path for path in folder.glob(f"{APP_EXE_STEM}-*") if path.is_file()]
    if matches:
        return str(max(matches, key=lambda path: path.stat().st_mtime))
    return ""


def find_packaged_service_exe(folder: Path | None = None) -> str:
    return find_packaged_exe(folder)


def default_program_exe() -> str:
    if is_frozen():
        here = Path(sys.executable).resolve()
        if here.is_file():
            return str(here)
        return find_packaged_exe(here.parent)
    exe = Path(sys.executable)
    if exe.is_file():
        return str(exe)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        conda = Path(local) / "anaconda3" / "envs" / "winstonlutz" / "python.exe"
        if conda.is_file():
            return str(conda)
    found = shutil.which("python")
    return found or ""


def default_python_exe() -> str:
    return default_program_exe()


def default_account() -> str:
    domain = str(os.environ.get("USERDOMAIN") or "").strip()
    user = str(os.environ.get("USERNAME") or "").strip()
    if domain and user and domain.upper() not in ("", user.upper()):
        return f"{domain}\\{user}"
    return user


def find_nssm() -> str:
    found = shutil.which("nssm") or shutil.which("nssm.exe")
    if found:
        return found
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(r"C:\nssm"),
        app_dir(),
        app_dir() / "nssm",
    ]
    names = [
        Path("nssm.exe"),
        Path("win64") / "nssm.exe",
        Path("win32") / "nssm.exe",
        Path("nssm") / "win64" / "nssm.exe",
        Path("nssm") / "nssm.exe",
    ]
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return str(path)
    return ""


def default_plan() -> WatchServicePlan:
    app = app_dir()
    settings = user_config_path()
    program = default_program_exe()
    return WatchServicePlan(
        nssm_exe=find_nssm(),
        program_exe=program,
        app_parameters=default_app_parameters(program),
        app_directory=str(app),
        settings_file=str(settings if settings.is_file() else app / SETTINGS_NAME),
        account=default_account(),
    )


def log_paths(app_directory: str) -> tuple[str, str]:
    logs = Path(app_directory) / "_logs"
    return str(logs / "watch_stdout.log"), str(logs / "watch_stderr.log")


def nssm_commands(plan: WatchServicePlan) -> list[list[str]]:
    """NSSM argv lists to install and configure the watch service (no password in set ObjectName)."""
    name = plan.service_name.strip() or DEFAULT_SERVICE_NAME
    nssm = plan.nssm_exe.strip() or "nssm"
    program_exe = plan.program_exe.strip()
    app_directory = plan.app_directory.strip()
    settings_file = plan.settings_file.strip()
    parameters = plan.app_parameters.strip()
    if not parameters:
        parameters = default_app_parameters(program_exe)
    stdout_log, stderr_log = log_paths(app_directory)
    commands: list[list[str]] = []
    if plan.replace_existing:
        commands.append([nssm, "stop", name])
        commands.append([nssm, "remove", name, "confirm"])
    commands.append([nssm, "install", name, program_exe])
    commands.append([nssm, "set", name, "AppDirectory", app_directory])
    if parameters:
        commands.append([nssm, "set", name, "AppParameters", parameters])
    commands.append(
        [nssm, "set", name, "AppEnvironmentExtra", f"WINSTONLUTZ_APP_CONFIG={settings_file}"]
    )
    commands.append(
        [nssm, "set", name, "DisplayName", plan.display_name.strip() or DEFAULT_DISPLAY_NAME]
    )
    commands.append([nssm, "set", name, "Start", "SERVICE_AUTO_START"])
    commands.append([nssm, "set", name, "AppStdout", stdout_log])
    commands.append([nssm, "set", name, "AppStderr", stderr_log])
    commands.append([nssm, "set", name, "AppRotateFiles", "1"])
    account = plan.account.strip()
    if account:
        commands.append([nssm, "set", name, "ObjectName", account, plan.password])
    if plan.start_after:
        commands.append([nssm, "start", name])
    return commands


def format_nssm_commands(plan: WatchServicePlan) -> str:
    """Human-readable commands with the account password hidden."""
    lines: list[str] = []
    for args in nssm_commands(plan):
        shown = list(args)
        if len(shown) >= 5 and shown[3].lower() == "objectname":
            shown[-1] = "<password>"
        quoted = []
        for part in shown:
            if any(ch.isspace() for ch in part) or not part:
                quoted.append(f'"{part}"')
            else:
                quoted.append(part)
        lines.append(" ".join(quoted))
    return "\n".join(lines)


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
    )


def install_watch_service(plan: WatchServicePlan) -> tuple[bool, str]:
    """Run NSSM to install the watch service. Returns (ok, combined log)."""
    nssm = Path(plan.nssm_exe.strip())
    if not nssm.is_file():
        return False, "nssm.exe was not found. Install NSSM from https://nssm.cc and try again."
    program_exe = Path(plan.program_exe.strip())
    if not program_exe.is_file():
        return False, f"Program was not found:\n{program_exe}"
    app_directory = Path(plan.app_directory.strip())
    if not app_directory.is_dir():
        return False, f"App directory was not found:\n{app_directory}"
    settings_file = Path(plan.settings_file.strip())
    if not settings_file.is_file():
        return False, f"Settings file was not found:\n{settings_file}"
    logs = app_directory / "_logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create log folder {logs}: {exc}"

    chunks: list[str] = []
    commands = nssm_commands(plan)
    for args in commands:
        display = list(args)
        if len(display) >= 5 and display[3].lower() == "objectname":
            display[-1] = "<password>"
        label = " ".join(display)
        try:
            proc = _run(args)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{label}\n{exc}"
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        removing = len(args) >= 2 and args[1].lower() == "remove"
        stopping = len(args) >= 2 and args[1].lower() == "stop"
        if proc.returncode != 0 and not (plan.replace_existing and (removing or stopping)):
            detail = err or out or f"exit {proc.returncode}"
            chunks.append(f"{label}\n{detail}")
            return False, "\n\n".join(chunks)
        if out or err:
            chunks.append(f"{label}\n{(out or err)}")
        else:
            chunks.append(label)
    return True, "\n".join(chunks)
