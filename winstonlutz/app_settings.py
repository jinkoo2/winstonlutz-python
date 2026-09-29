"""GUI settings in winstonlutz.gui.settings.json next to the executable.

Machine list, data folders, BB methods, and tolerance live under MACHINES.
Clinic SMTP lives under Notifications.email. Window/level and view style stay in QSettings.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SETTINGS_NAME = "winstonlutz.gui.settings.json"
MACHINES_KEY = "MACHINES"
INSTITUTION_KEY = "Institution"
RUN_MODE_KEY = "RunMode"
RUN_MODE_CLINIC = "Clinic"
RUN_MODE_SIMPLE = "Simple"
RUN_MODES = (RUN_MODE_CLINIC, RUN_MODE_SIMPLE)
ERROR_EMAIL_TO_KEY = "error_email_to"
EVENT_EMAIL_TO_KEY = "event_email_to"
NEW_CASE_EMAIL_TO_KEY = "new_case_email_to"
NOTIFICATIONS_KEY = "Notifications"
WATCHER_KEY = "Watcher"
POST_PROCESSING_KEY = "PostProcessing"
DOCUFORMS2_IGRT_TYPE = "docuforms2_igrt"
TOP_LEVEL_EMAIL_KEYS = (
    ERROR_EMAIL_TO_KEY,
    EVENT_EMAIL_TO_KEY,
    NEW_CASE_EMAIL_TO_KEY,
    "email_from",
    "email_domain",
    "email_host_address",
    "email_host_port",
    "enable_ssl",
    "email_from_enc_pw",
)
DEFAULT_CASE_FOLDER_REGEX = r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"
DEFAULT_WATCH_FILE_PATTERNS = ["RE.*.dcm"]
DEFAULT_WATCH_RECURSIVE = True
DEFAULT_WATCH_CASE_DIR_LEVELS = 1
DEFAULT_WATCH_POLL_SEC = 10.0


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


def named_machines(machines) -> list[dict]:
    if not isinstance(machines, list):
        return []
    return [m for m in machines if isinstance(m, dict) and str(m.get("NAME") or "").strip()]


def notifications_block(data: dict | None) -> dict:
    notes = (data or {}).get(NOTIFICATIONS_KEY)
    return notes if isinstance(notes, dict) else {}


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def normalize_file_patterns(value) -> list[str]:
    """Watcher globs: JSON array, comma/newline string, or default ``RE.*.dcm``."""
    chunks: list[str] = []
    if isinstance(value, (list, tuple)):
        chunks.extend(str(item or "") for item in value)
    elif value is not None and str(value).strip():
        chunks.append(str(value))
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in chunk.replace(";", "\n").splitlines():
            for part in line.split(","):
                pat = part.strip()
                if not pat or pat in seen:
                    continue
                seen.add(pat)
                out.append(pat)
    return out or list(DEFAULT_WATCH_FILE_PATTERNS)


def watcher_settings(data: dict | None = None) -> dict:
    """Paths and match rules for ``winstonlutz watch``."""
    settings = data if data is not None else load_gui_settings()
    block = (settings or {}).get(WATCHER_KEY)
    raw = block if isinstance(block, dict) else {}
    if "case_folder_regex" in raw:
        case_regex = str(raw.get("case_folder_regex") or "")
    else:
        case_regex = DEFAULT_CASE_FOLDER_REGEX
    try:
        levels = int(raw.get("case_dir_levels", DEFAULT_WATCH_CASE_DIR_LEVELS))
    except (TypeError, ValueError):
        levels = DEFAULT_WATCH_CASE_DIR_LEVELS
    levels = max(1, min(levels, 8))
    try:
        poll = float(raw.get("poll_sec", DEFAULT_WATCH_POLL_SEC))
    except (TypeError, ValueError):
        poll = DEFAULT_WATCH_POLL_SEC
    if poll <= 0:
        poll = DEFAULT_WATCH_POLL_SEC
    return {
        "watch_path": str(raw.get("watch_path") or "").strip(),
        "data_root": str(raw.get("data_root") or "").strip(),
        "recursive": _as_bool(raw.get("recursive"), DEFAULT_WATCH_RECURSIVE),
        "file_patterns": normalize_file_patterns(
            raw.get("file_patterns", raw.get("file_pattern"))
        ),
        "case_folder_regex": case_regex,
        "case_dir_levels": levels,
        "poll_sec": poll,
    }


def default_docuforms2_igrt_step() -> dict:
    """Clinic DocuForms2 upload (upload_igrt input.json)."""
    return {
        "type": DOCUFORMS2_IGRT_TYPE,
        "enabled": True,
        "backend_url": "https://roweb3.uhmc.sbuh.stonybrook.edu:9001",
        "verify_ssl": False,
        "dry_run": False,
        "attach_dcm_zip": True,
        "attach_pdf": False,
        "resubmit": False,
        "timeout_sec": 300,
    }


def post_processing_steps(data: dict | None = None) -> list[dict]:
    settings = data if data is not None else load_gui_settings()
    raw = (settings or {}).get(POST_PROCESSING_KEY)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [step for step in raw if isinstance(step, dict) and str(step.get("type") or "").strip()]


def find_post_step(type_name: str, data: dict | None = None) -> dict:
    wanted = str(type_name or "").strip()
    for step in post_processing_steps(data):
        if str(step.get("type") or "").strip() == wanted:
            return dict(step)
    return {}


def upsert_post_step(steps: list, step: dict) -> list[dict]:
    kind = str((step or {}).get("type") or "").strip()
    out: list[dict] = []
    found = False
    for existing in steps or []:
        if not isinstance(existing, dict):
            continue
        if str(existing.get("type") or "").strip() == kind:
            out.append(dict(step))
            found = True
        else:
            out.append(dict(existing))
    if not found and kind:
        out.append(dict(step))
    return out


def email_settings_block(data: dict | None) -> dict:
    """Email fields from Notifications.email, else top-level keys."""
    notes = notifications_block(data)
    nested = notes.get("email")
    if isinstance(nested, dict) and any(_setting_nonempty(v) for v in nested.values()):
        merged = dict(data or {})
        merged.update(nested)
        return merged
    return dict(data or {})


def _setting_nonempty(value) -> bool:
    if isinstance(value, (list, tuple)):
        return any(str(x or "").strip() for x in value)
    return bool(str(value or "").strip())


def chat_webhook_urls(data: dict | None = None) -> dict[str, str]:
    """Incoming webhook URLs; empty string means that channel is off."""
    settings = data if data is not None else load_gui_settings()
    notes = notifications_block(settings)
    channels = (
        ("google_chat", "google_chat_webhook_url"),
        ("slack", "slack_webhook_url"),
        ("microsoft_teams", "teams_webhook_url"),
        ("discord", "discord_webhook_url"),
    )
    out: dict[str, str] = {}
    for name, top_key in channels:
        block = notes.get(name)
        nested = ""
        if isinstance(block, dict):
            nested = str(block.get("webhook_url") or "").strip()
        top = str((settings or {}).get(top_key) or "").strip()
        out[name] = nested or top
    return out


def get_run_mode(data: dict | None = None) -> str:
    settings = data if data is not None else load_gui_settings()
    text = str((settings or {}).get(RUN_MODE_KEY) or "").strip()
    if text.lower() == RUN_MODE_SIMPLE.lower():
        return RUN_MODE_SIMPLE
    return RUN_MODE_CLINIC


def is_simple_run_mode(data: dict | None = None) -> bool:
    """True when Open Case should pick an RI folder instead of a machine list.

    Simple mode if the settings file is missing, MACHINES is absent/empty, or
    ``RunMode`` is ``Simple``.
    """
    path = user_config_path()
    if not path.is_file():
        return True
    settings = data if data is not None else load_gui_settings()
    if not settings:
        return True
    if get_run_mode(settings) == RUN_MODE_SIMPLE:
        return True
    return not named_machines(settings.get(MACHINES_KEY))


def simple_machine_name(case_folder: str | Path) -> str:
    """Machine name: parent of the case folder.

    ``Edge/26-09-24_...`` → ``Edge``. If that parent is named ``Data``,
    the grandparent is used (``Edge/Data/26-09-24_...`` → ``Edge``).
    """
    folder = Path(case_folder)
    parent = folder.parent
    name = parent.name
    if name.lower() == "data":
        name = parent.parent.name
    if not name or name in (".", ""):
        return ""
    return name


def get_machines() -> list[dict]:
    machines = load_gui_settings().get(MACHINES_KEY) or []
    if not isinstance(machines, list):
        return []
    return [m for m in machines if isinstance(m, dict)]


def find_machine_by_name(name: str) -> dict | None:
    want = str(name or "").strip().lower()
    if not want:
        return None
    for machine in named_machines(get_machines()):
        if str(machine.get("NAME") or "").strip().lower() == want:
            return machine
    return None


def find_machine_for_folder(folder: str | Path) -> dict | None:
    """Match a case or machine folder to a MACHINES entry."""
    folder = Path(folder)
    try:
        folder = folder.resolve()
    except OSError:
        pass
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
    return find_machine_by_name(simple_machine_name(folder))


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
        "new_case_email_to": [],
        "docuforms2_form_id": "",
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
