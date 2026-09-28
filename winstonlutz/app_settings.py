"""GUI settings in winstonlutz.gui.settings.json next to the executable.

Machine list, data folders, BB methods, and tolerance live under MACHINES.
Clinic email/tools stay in each machine tree's app.config.txt.
Window/level and view style stay in QSettings.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SETTINGS_NAME = "winstonlutz.gui.settings.json"
MACHINES_KEY = "MACHINES"
INSTITUTION_KEY = "Institution"
DEFAULT_CASE_FOLDER_REGEX = r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"


def app_dir() -> Path:
    """Directory the running program was loaded from."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] not in ("", "-c") else Path()
    try:
        argv0 = argv0.expanduser().resolve()
    except OSError:
        argv0 = Path()
    if argv0.suffix.lower() == ".exe" and argv0.is_file():
        return argv0.parent
    pkg = Path(__file__).resolve().parent
    root = pkg.parent
    if root.name.lower() in ("site-packages", "dist-packages"):
        return argv0.parent if argv0.is_file() else Path.cwd()
    return root


def user_config_path() -> Path:
    override = os.environ.get("WINSTONLUTZ_APP_CONFIG", "").strip()
    if override:
        return Path(override)
    return app_dir() / SETTINGS_NAME


def load_gui_settings() -> dict:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    data.pop("DATA_ROOT_FOLDER", None)
    return data


def save_gui_settings(data: dict) -> None:
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def get_app_param(key: str):
    value = load_gui_settings().get(key)
    if value is None:
        return ""
    return value


def set_app_param(key: str, value) -> None:
    data = load_gui_settings()
    data[key] = value
    save_gui_settings(data)


def get_institution() -> str:
    return str(get_app_param(INSTITUTION_KEY) or "").strip()


def get_machines() -> list[dict]:
    machines = load_gui_settings().get(MACHINES_KEY) or []
    if not isinstance(machines, list):
        return []
    return [m for m in machines if isinstance(m, dict)]


def find_machine_for_folder(folder: str | Path) -> dict | None:
    """Match a case or machine folder to a MACHINES entry."""
    folder = Path(folder)
    try:
        folder = folder.resolve()
    except OSError:
        return None
    for machine in get_machines():
        data = str(machine.get("DATA_FOLDER") or "").strip()
        if data:
            data_path = Path(data).expanduser()
            try:
                data_path = data_path.resolve()
            except OSError:
                data_path = Path(data)
            try:
                folder.relative_to(data_path)
                return machine
            except ValueError:
                pass
        name = str(machine.get("NAME") or "").strip()
        if not name:
            continue
        here = folder
        for _ in range(3):
            if here.name == name:
                return machine
            here = here.parent
    return None


BB_SEARCH_METHODS = ("ConnectedComponent", "LoG", "OtsuThreshold")
MV_FIELD_SEARCH_METHODS = ("Otsu",)
KV_FIELD_SEARCH_METHODS = ("ImageCenter",)
DEFAULT_NOMINAL_GANTRY = [270, 0, 90, 180]
DEFAULT_NOMINAL_TABLE = [90, 45, 0, 315, 270]
DEFAULT_NOMINAL_COLLIMATOR = [135, 90, 45, 0, 315, 270, 225]


def format_csv_numbers(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            try:
                number = float(item)
            except (TypeError, ValueError):
                parts.append(str(item))
                continue
            parts.append(str(int(number) if number == int(number) else number))
        return ", ".join(parts)
    try:
        number = float(value)
        return str(int(number) if number == int(number) else number)
    except (TypeError, ValueError):
        return str(value)


def parse_csv_numbers(text) -> list:
    raw = str(text or "").strip()
    if not raw:
        return []
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = float(part)
        except ValueError:
            continue
        out.append(int(number) if number == int(number) else number)
    return out


def parse_int_list(text) -> list[int]:
    return [int(x) for x in parse_csv_numbers(text)]


def default_machine(name: str = "New machine") -> dict:
    """Template for a new MACHINES entry (documented analysis defaults)."""
    return {
        "NAME": name,
        "DATA_FOLDER": "",
        "REPORT_TEMPLATE_FILE_PATH": "",
        "CASE_FOLDER_NAME_REGEX": DEFAULT_CASE_FOLDER_REGEX,
        "WL_pass_tolerance": 1.0,
        "record_csv_file": "",
        "MV_bb_search_method": "ConnectedComponent",
        "kV_bb_search_method": "ConnectedComponent",
        "MV_field_search_method": "Otsu",
        "kV_field_search_method": "ImageCenter",
        "crop_mm": 50.0,
        "sad_mm": 1000.0,
        "default_sid_mm": 1500.0,
        "MV_kvp_min": 1000.0,
        "MV_image_size": [1190, 1190],
        "kV_image_size": [1024, 768],
        "nominal_gantry_angles": list(DEFAULT_NOMINAL_GANTRY),
        "nominal_table_angles": list(DEFAULT_NOMINAL_TABLE),
        "nominal_collimator_angles": list(DEFAULT_NOMINAL_COLLIMATOR),
        "DICOM_PLAN_FILE": "",
        "IGNORE_BEAMS": [],
        "ALL_RI_IMAGE_REQUIRED": False,
    }


def report_template_root(machine: dict) -> Path | None:
    text = str(machine.get("REPORT_TEMPLATE_FILE_PATH") or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        if path.parent.name.lower() in ("full", "short"):
            return path.parent.parent
        return path.parent
    if path.is_dir():
        return path
    return None
