"""Move old case folders from each machine WATCH_FOLDER to DATA_FOLDER."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from .app_settings import DEFAULT_CASE_FOLDER_REGEX, named_machines

logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_AT = "01:00"
DEFAULT_ARCHIVE_AGE_DAYS = 7
DEFAULT_ARCHIVE_WINDOW_HOURS = 3.0
_CASE_NAME_FMT = "%y-%m-%d_%H-%M-%S"


@dataclass
class ArchiveResult:
    moved: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"moved={len(self.moved)} skipped={len(self.skipped)} errors={len(self.errors)}"
        ]
        for src, dest in self.moved:
            lines.append(f"moved {src} -> {dest}")
        for path, reason in self.skipped:
            lines.append(f"skipped {path}: {reason}")
        for path, reason in self.errors:
            lines.append(f"error {path}: {reason}")
        return "\n".join(lines)


def parse_archive_at(value, default: str = DEFAULT_ARCHIVE_AT) -> time:
    """Parse ``HH:MM`` or ``HH:MM:SS`` into a local clock time."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or "").strip() or default
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    try:
        return datetime.strptime(default, "%H:%M").time()
    except ValueError:
        return time(1, 0)


def format_archive_at(value) -> str:
    parsed = parse_archive_at(value)
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def next_archive_datetime(
    run_at: time,
    now: datetime | None = None,
    window_hours: float = DEFAULT_ARCHIVE_WINDOW_HOURS,
) -> datetime:
    """Next archive fire time.

    Inside tonight's window (``run_at`` plus *window_hours*) this returns *now*
    so a service restart at 1:15 still archives. After the window it waits
    until tomorrow so clinic hours are not used for catch-up.
    """
    now = now or datetime.now()
    run_at = parse_archive_at(run_at)
    hours = window_hours if window_hours > 0 else DEFAULT_ARCHIVE_WINDOW_HOURS
    today_start = datetime.combine(now.date(), run_at)
    today_end = today_start + timedelta(hours=hours)
    if now < today_start:
        return today_start
    if now < today_end:
        return now.replace(microsecond=0)
    return today_start + timedelta(days=1)


def case_folder_datetime(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, _CASE_NAME_FMT)
    except ValueError:
        return None


def case_is_older_than(
    case_dir: str | Path,
    age_days: int,
    now: datetime | None = None,
) -> bool:
    """True when the case date is at least *age_days* before *now*."""
    now = now or datetime.now()
    days = max(1, int(age_days))
    path = Path(case_dir)
    parsed = case_folder_datetime(path.name)
    if parsed is not None:
        return (now.date() - parsed.date()).days >= days
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    return (now - mtime) >= timedelta(days=days)


def _norm_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _same_dir(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return _norm_key(left) == _norm_key(right)


def _iter_case_dirs(root: Path, pattern: str) -> list[Path]:
    found: list[Path] = []
    try:
        compiled = re.compile(pattern)
    except re.error:
        compiled = re.compile(DEFAULT_CASE_FOLDER_REGEX)
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if not compiled.fullmatch(entry.name):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        found.append(Path(entry.path))
                except OSError:
                    continue
    except OSError as exc:
        logger.warning("cannot list %s: %s", root, exc)
    return found


def archive_old_cases(
    machines,
    *,
    age_days: int = DEFAULT_ARCHIVE_AGE_DAYS,
    now: datetime | None = None,
    skip_keys: set[str] | None = None,
    case_folder_regex: str | None = None,
    dry_run: bool = False,
) -> ArchiveResult:
    """Move case folders older than *age_days* from WATCH_FOLDER to DATA_FOLDER."""
    now = now or datetime.now()
    days = max(1, int(age_days))
    pattern = str(case_folder_regex or "").strip() or DEFAULT_CASE_FOLDER_REGEX
    skip = {_norm_key(Path(item)) for item in (skip_keys or ())}
    result = ArchiveResult()
    for machine in named_machines(machines if isinstance(machines, list) else []):
        name = str(machine.get("NAME") or "").strip()
        watch_text = str(machine.get("WATCH_FOLDER") or "").strip()
        data_text = str(machine.get("DATA_FOLDER") or "").strip()
        if not watch_text or not data_text:
            continue
        watch = Path(watch_text).expanduser()
        data = Path(data_text).expanduser()
        if _same_dir(watch, data):
            result.skipped.append((f"{name}:{watch}", "WATCH_FOLDER and DATA_FOLDER are the same"))
            continue
        if not watch.is_dir():
            result.skipped.append((f"{name}:{watch}", "WATCH_FOLDER is not a directory"))
            continue
        if not data.is_dir():
            result.skipped.append((f"{name}:{data}", "DATA_FOLDER is not a directory"))
            continue
        machine_regex = str(machine.get("CASE_FOLDER_NAME_REGEX") or "").strip() or pattern
        for case_dir in _iter_case_dirs(watch, machine_regex):
            src_key = _norm_key(case_dir)
            if src_key in skip:
                result.skipped.append((str(case_dir), "in use"))
                continue
            if not case_is_older_than(case_dir, days, now=now):
                continue
            dest = data / case_dir.name
            if dest.exists():
                result.skipped.append((str(case_dir), f"already in DATA_FOLDER: {dest}"))
                continue
            if dry_run:
                result.moved.append((str(case_dir), str(dest)))
                continue
            try:
                shutil.move(str(case_dir), str(dest))
            except OSError as exc:
                logger.exception("archive failed %s -> %s", case_dir, dest)
                result.errors.append((str(case_dir), str(exc)))
                continue
            logger.info("Archived %s -> %s", case_dir, dest)
            result.moved.append((str(case_dir), str(dest)))
    return result
