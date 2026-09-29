"""Email users when a new QA case folder appears under a machine."""

from __future__ import annotations

import html
import json
import logging
import threading
from pathlib import Path

from .app_settings import get_institution, get_machines, named_machines, simple_machine_name
from .emailer import send, smtp_settings
from .identity import profile_email, subscribers_for_new_qa_case, users_dir
from .pipeline import list_case_folders

logger = logging.getLogger(__name__)

NOTIFIED_NAME = "notified_cases.json"
_lock = threading.Lock()


def _state_path() -> Path:
    return users_dir() / NOTIFIED_NAME


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {"seeded": False, "cases": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"seeded": False, "cases": []}
    if not isinstance(data, dict):
        return {"seeded": False, "cases": []}
    cases = data.get("cases")
    if not isinstance(cases, list):
        cases = []
    return {"seeded": bool(data.get("seeded")), "cases": [str(x) for x in cases if str(x).strip()]}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def case_notify_key(machine_name: str, case_folder: str | Path) -> str:
    return f"{str(machine_name or '').strip()}/{Path(case_folder).name}"


def _current_case_keys() -> list[str]:
    keys: list[str] = []
    for machine in named_machines(get_machines()):
        name = str(machine.get("NAME") or "").strip()
        for folder in list_case_folders(machine):
            keys.append(case_notify_key(name, folder))
    return keys


def _seed_unlocked(exclude_key: str | None = None) -> None:
    state = _load_state()
    if state.get("seeded"):
        return
    keys = [key for key in _current_case_keys() if key != exclude_key]
    seen = list(dict.fromkeys([*(state.get("cases") or []), *keys]))
    _save_state({"seeded": True, "cases": seen})


def seed_existing_cases() -> None:
    """Remember cases already on disk so they do not generate emails."""
    with _lock:
        _seed_unlocked()


def _already_seen(key: str) -> bool:
    return key in set(_load_state().get("cases") or [])


def _mark_seen(key: str) -> None:
    state = _load_state()
    cases = list(state.get("cases") or [])
    if key not in cases:
        cases.append(key)
    _save_state({"seeded": True, "cases": cases})


def _case_email_body(machine_name: str, case_folder: Path) -> str:
    institution = html.escape(get_institution() or "")
    machine = html.escape(machine_name or "")
    case_name = html.escape(case_folder.name)
    folder = html.escape(str(case_folder))
    return (
        "<html><body>"
        "<p>A new Winston-Lutz QA case is available.</p>"
        "<table>"
        f"<tr><td>Institution</td><td>{institution or '—'}</td></tr>"
        f"<tr><td>Machine</td><td>{machine or '—'}</td></tr>"
        f"<tr><td>Case</td><td>{case_name}</td></tr>"
        f"<tr><td>Folder</td><td>{folder}</td></tr>"
        "</table>"
        "</body></html>"
    )


def notify_new_qa_case(machine_name: str, case_folder: str | Path, *, send_mail: bool = True) -> bool:
    """Notify subscribers once per machine/case. Returns True if this case was new."""
    folder = Path(case_folder)
    name = str(machine_name or "").strip() or simple_machine_name(folder)
    if not name or not folder.name:
        return False
    key = case_notify_key(name, folder)
    with _lock:
        _seed_unlocked(exclude_key=key)
        if _already_seen(key):
            return False
        _mark_seen(key)
    if not send_mail:
        return True
    recipients = [profile_email(p) for p in subscribers_for_new_qa_case(name)]
    if not recipients:
        return True
    smtp = smtp_settings()
    if smtp is None:
        logger.info("new-case email skipped: SMTP from/host not configured")
        return True
    try:
        send(
            from_user=smtp["from_user"],
            from_enc_pw=smtp["from_enc_pw"],
            to=recipients,
            subject=f"New WL QA case: {name} {folder.name}",
            body=_case_email_body(name, folder),
            domain=smtp["domain"],
            host=smtp["host"],
            port=smtp["port"],
            enable_ssl=smtp["enable_ssl"],
        )
    except Exception:
        logger.warning("new-case email failed for %s", key, exc_info=True)
    return True


def scan_and_notify_new_cases(*, send_mail: bool = True) -> list[str]:
    """Seed on first run; email subscribers for case folders not seen before."""
    discovered: list[str] = []
    seed_existing_cases()
    for machine in named_machines(get_machines()):
        name = str(machine.get("NAME") or "").strip()
        for folder in list_case_folders(machine):
            if notify_new_qa_case(name, folder, send_mail=send_mail):
                discovered.append(case_notify_key(name, folder))
    return discovered


def scan_and_notify_new_cases_async() -> None:
    def _run() -> None:
        try:
            scan_and_notify_new_cases()
        except Exception:
            logger.exception("new-case scan failed")

    threading.Thread(target=_run, daemon=True).start()
