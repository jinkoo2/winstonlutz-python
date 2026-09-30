"""App settings in settings.json next to the executable.

Machine list, data folders, BB methods, and tolerance live under MACHINES.
Clinic SMTP lives under Notifications.email. Window/level and view style stay in QSettings.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SETTINGS_NAME = "settings.json"
LEGACY_SETTINGS_NAME = "winstonlutz.gui.settings.json"
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
DEFAULT_WATCH_DISK_SCAN = True
DEFAULT_WATCH_DISK_SCAN_SEC = 60.0
DEFAULT_WATCH_ARCHIVE = True
DEFAULT_WATCH_ARCHIVE_AGE_DAYS = 7
DEFAULT_WATCH_ARCHIVE_AT = "01:00"
# Canonical Watcher JSON keys, with older names still accepted when reading.
WATCHER_SETTING_ALIASES = {
    "winstonlutz_data_root": ("data_root",),
    "watch_subfolders": ("recursive",),
    "new_case_file_patterns": ("file_patterns", "file_pattern"),
    "case_folder_name_regex": ("case_folder_regex",),
    "machine_to_case_dir_levels": ("case_dir_levels",),
    "queued_case_poll_sec": ("poll_sec",),
    "disk_scan_for_new_case_detection": ("disk_scan",),
    "disk_scan_for_new_case_detection_sec": ("disk_scan_sec",),
    "archive_old_cases": ("archive",),
    "archive_cases_older_than_days": ("archive_age_days",),
    "archive_old_cases_at": ("archive_at",),
}


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


def user_config_path(*, writing: bool = False) -> Path:
    """``settings.json`` next to the exe (or project root from source).

    ``WINSTONLUTZ_APP_CONFIG`` overrides the path. An older
    ``winstonlutz.gui.settings.json`` in the same folder is still read if
    ``settings.json`` is missing. Saves always go to ``settings.json``.
    """
    override = os.environ.get("WINSTONLUTZ_APP_CONFIG", "").strip()
    if override:
        return Path(override)
    folder = app_dir()
    canonical = folder / SETTINGS_NAME
    if writing or canonical.is_file():
        return canonical
    legacy = folder / LEGACY_SETTINGS_NAME
    if legacy.is_file():
        return legacy
    return canonical


def strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments; leave JSON string contents unchanged."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    in_line = False
    in_block = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if ch in "\r\n":
                in_line = False
                out.append(ch)
            i += 1
            continue
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            if ch in "\r\n":
                out.append(ch)
            i += 1
            continue
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_gui_settings() -> dict:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    data.pop("DATA_ROOT_FOLDER", None)
    return data


def save_gui_settings(data: dict) -> None:
    path = user_config_path(writing=True)
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


def promote_watcher_aliases(raw: dict | None) -> dict:
    """Copy Watcher settings and fill canonical keys from older aliases."""
    out = dict(raw) if isinstance(raw, dict) else {}
    for new_key, old_keys in WATCHER_SETTING_ALIASES.items():
        if new_key in out:
            continue
        for old_key in old_keys:
            if old_key in out:
                out[new_key] = out[old_key]
                break
    return out


def watcher_settings(data: dict | None = None) -> dict:
    """Paths and match rules for ``winstonlutz watch``."""
    settings = data if data is not None else load_gui_settings()
    block = (settings or {}).get(WATCHER_KEY)
    raw = promote_watcher_aliases(block if isinstance(block, dict) else {})
    if "case_folder_name_regex" in raw:
        case_regex = str(raw.get("case_folder_name_regex") or "")
    else:
        case_regex = DEFAULT_CASE_FOLDER_REGEX
    try:
        levels = int(raw.get("machine_to_case_dir_levels", DEFAULT_WATCH_CASE_DIR_LEVELS))
    except (TypeError, ValueError):
        levels = DEFAULT_WATCH_CASE_DIR_LEVELS
    levels = max(1, min(levels, 8))
    try:
        poll = float(raw.get("queued_case_poll_sec", DEFAULT_WATCH_POLL_SEC))
    except (TypeError, ValueError):
        poll = DEFAULT_WATCH_POLL_SEC
    if poll <= 0:
        poll = DEFAULT_WATCH_POLL_SEC
    try:
        disk_scan_sec = float(
            raw.get("disk_scan_for_new_case_detection_sec", DEFAULT_WATCH_DISK_SCAN_SEC)
        )
    except (TypeError, ValueError):
        disk_scan_sec = DEFAULT_WATCH_DISK_SCAN_SEC
    if disk_scan_sec <= 0:
        disk_scan_sec = DEFAULT_WATCH_DISK_SCAN_SEC
    try:
        archive_age_days = int(
            raw.get("archive_cases_older_than_days", DEFAULT_WATCH_ARCHIVE_AGE_DAYS)
        )
    except (TypeError, ValueError):
        archive_age_days = DEFAULT_WATCH_ARCHIVE_AGE_DAYS
    if archive_age_days < 1:
        archive_age_days = DEFAULT_WATCH_ARCHIVE_AGE_DAYS
    return {
        "watch_path": str(raw.get("watch_path") or "").strip(),
        "winstonlutz_data_root": str(raw.get("winstonlutz_data_root") or "").strip(),
        "watch_subfolders": _as_bool(raw.get("watch_subfolders"), DEFAULT_WATCH_RECURSIVE),
        "new_case_file_patterns": normalize_file_patterns(
            raw.get("new_case_file_patterns")
        ),
        "case_folder_name_regex": case_regex,
        "machine_to_case_dir_levels": levels,
        "queued_case_poll_sec": poll,
        "disk_scan_for_new_case_detection": _as_bool(
            raw.get("disk_scan_for_new_case_detection"), DEFAULT_WATCH_DISK_SCAN
        ),
        "disk_scan_for_new_case_detection_sec": disk_scan_sec,
        "archive_old_cases": _as_bool(raw.get("archive_old_cases"), DEFAULT_WATCH_ARCHIVE),
        "archive_cases_older_than_days": archive_age_days,
        "archive_old_cases_at": _normalize_hhmm(
            raw.get("archive_old_cases_at"), DEFAULT_WATCH_ARCHIVE_AT
        ),
    }


def _normalize_hhmm(value, default: str) -> str:
    text = str(value or "").strip() or default
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError, IndexError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def default_docuforms2_igrt_step() -> dict:
    """Clinic DocuForms2 upload (upload_igrt input.json)."""
    return {
        "type": DOCUFORMS2_IGRT_TYPE,
        "enabled": True,
        "backend_url": "https://roweb3.uhmc.sbuh.stonybrook.edu:9001",
        "verify_ssl": False,
        "dry_run": False,
        "attach_dcm_zip": True,
        "attach_pdf": True,
        "resubmit": False,
        "timeout_sec": 300,
        "form_ids": [],
        "email_success_event_to": [],
        "email_failure_event_to": [],
    }


def normalize_form_ids(value) -> list[dict]:
    """Normalize a machine→form_id list to ``[{"machine": name, "form_id": id}, ...]``."""
    items: list = []
    if isinstance(value, dict):
        if "machine" in value or "form_id" in value or "NAME" in value:
            items = [value]
        else:
            items = [{"machine": key, "form_id": val} for key, val in value.items()]
    elif isinstance(value, list):
        items = value
    out: list[dict] = []
    for item in items:
        machine = ""
        form_id = ""
        if isinstance(item, dict):
            machine = str(item.get("machine") or item.get("NAME") or item.get("name") or "").strip()
            form_id = str(item.get("form_id") or item.get("docuforms2_form_id") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            machine = str(item[0] or "").strip()
            form_id = str(item[1] or "").strip()
        if not machine or not form_id:
            continue
        key = machine.lower()
        out = [row for row in out if row["machine"].lower() != key]
        out.append({"machine": machine, "form_id": form_id})
    return out


def form_ids_from_step(step: dict | None) -> list[dict]:
    step = step or {}
    return normalize_form_ids(step.get("form_ids") or step.get("form_id_map"))


def form_ids_from_machines(machines) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(machines, list):
        return rows
    for machine in machines:
        if not isinstance(machine, dict):
            continue
        name = str(machine.get("NAME") or "").strip()
        form_id = str(machine.get("docuforms2_form_id") or "").strip()
        if name and form_id:
            rows.append({"machine": name, "form_id": form_id})
    return normalize_form_ids(rows)


def form_id_for_machine(step: dict | None, machine_cfg: dict | None) -> str:
    """DocuForms2 form id for this machine from PostProcessing.form_ids, else legacy machine key."""
    name = str((machine_cfg or {}).get("NAME") or "").strip()
    for row in form_ids_from_step(step):
        if row["machine"].lower() == name.lower():
            return row["form_id"]
    return str((machine_cfg or {}).get("docuforms2_form_id") or "").strip()


def format_form_ids(value) -> str:
    return "\n".join(
        f"{row['machine']} = {row['form_id']}" for row in normalize_form_ids(value)
    )


def parse_form_ids_text(text) -> list[dict]:
    rows: list[dict] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            left, right = line.split("=", 1)
        elif ":" in line:
            left, right = line.split(":", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            left, right = parts
        machine = left.strip()
        form_id = right.strip()
        if machine and form_id:
            rows.append({"machine": machine, "form_id": form_id})
    return normalize_form_ids(rows)


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


def machine_configured_roots(machine: dict | None) -> list[Path]:
    """WATCH_FOLDER then DATA_FOLDER as configured (paths may not exist)."""
    roots: list[Path] = []
    seen: set[str] = set()
    if not isinstance(machine, dict):
        return roots
    for key in ("WATCH_FOLDER", "DATA_FOLDER"):
        text = str(machine.get(key) or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        ident = str(path).replace("\\", "/").rstrip("/").lower()
        if ident in seen:
            continue
        seen.add(ident)
        roots.append(path)
    return roots


def machine_case_roots(machine: dict | None) -> list[Path]:
    """Existing WATCH_FOLDER then DATA_FOLDER directories for Open Case."""
    return [path for path in machine_configured_roots(machine) if path.is_dir()]


def find_machine_for_folder(folder: str | Path) -> dict | None:
    """Match a case or machine folder to a MACHINES entry."""
    folder = Path(folder)
    try:
        folder = folder.resolve()
    except OSError:
        pass
    for machine in get_machines():
        for root in machine_configured_roots(machine):
            try:
                root_path = root.resolve()
            except OSError:
                root_path = root
            try:
                folder.relative_to(root_path)
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
        "WATCH_FOLDER": "",
        "DATA_FOLDER": "",
        "REPORT_TEMPLATE_FILE_PATH": "",
        "CASE_FOLDER_NAME_REGEX": DEFAULT_CASE_FOLDER_REGEX,
        "WL_pass_tolerance": 1.0,
        "record_csv_file": "",
        "new_case_email_to": [],
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
