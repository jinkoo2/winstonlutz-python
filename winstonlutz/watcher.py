"""Watch a transfer folder for configured trigger files and queue case directories."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .app_settings import (
    DEFAULT_CASE_FOLDER_REGEX,
    DEFAULT_WATCH_ARCHIVE,
    DEFAULT_WATCH_ARCHIVE_AGE_DAYS,
    DEFAULT_WATCH_ARCHIVE_AT,
    DEFAULT_WATCH_CASE_DIR_LEVELS,
    DEFAULT_WATCH_DISK_SCAN,
    DEFAULT_WATCH_DISK_SCAN_SEC,
    DEFAULT_WATCH_FILE_PATTERNS,
    DEFAULT_WATCH_POLL_SEC,
    DEFAULT_WATCH_RECURSIVE,
    _as_bool,
    get_machines,
    promote_watcher_aliases,
)
from .archive import archive_old_cases, next_archive_datetime, parse_archive_at
from .emailer import send_error_email, send_event_email
from .worker import process_case

logger = logging.getLogger(__name__)


class WatchPathUnavailable(Exception):
    """watch_path or data_root cannot be opened (offline share, missing folder)."""


def folder_unavailable_reason(path: Path, label: str) -> str | None:
    """Return a message if *path* is missing or not a directory; otherwise None."""
    try:
        if path.is_dir():
            return None
        if path.exists():
            return f"{label} is not a directory: {path}"
        return f"{label} was not found: {path}"
    except OSError as exc:
        return f"{label} is not available: {path} ({exc})"


def _notify_unavailable(message: str, details: str = "") -> None:
    logger.error("%s", message)
    send_error_email(message, details, context="watch", blocking=True)
    raise WatchPathUnavailable(message)


def ensure_watch_folders(watch_path: Path, data_root: Path) -> None:
    for path, label in ((watch_path, "watch_path"), (data_root, "winstonlutz_data_root")):
        reason = folder_unavailable_reason(path, label)
        if reason:
            _notify_unavailable(reason)


def file_matches_patterns(name: str, patterns: list[str] | None) -> bool:
    pats = [str(p).strip() for p in (patterns or []) if str(p).strip()]
    if not pats:
        pats = list(DEFAULT_WATCH_FILE_PATTERNS)
    for pat in pats:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(name.lower(), pat.lower()):
            return True
    return False


def case_dir_from_file(path: Path, levels: int = DEFAULT_WATCH_CASE_DIR_LEVELS) -> Path:
    folder = Path(path).parent
    extra = max(1, int(levels)) - 1
    for _ in range(extra):
        folder = folder.parent
    return folder


def case_folder_matches(name: str, regex: str | None) -> bool:
    pattern = str(regex or "").strip()
    if not pattern:
        return True
    try:
        return re.fullmatch(pattern, name) is not None
    except re.error:
        logger.warning("invalid case_folder_regex %r", pattern)
        return False


def _iter_dirs(folder: Path):
    try:
        with os.scandir(folder) as it:
            for ent in it:
                try:
                    if ent.is_dir(follow_symlinks=False):
                        yield Path(ent.path)
                except OSError:
                    continue
    except OSError:
        return


def candidate_case_dirs(
    watch_path: Path,
    *,
    recursive: bool,
    case_folder_regex: str,
) -> list[Path]:
    """Case folders as direct children, or grandchildren when recursive (machine/case)."""
    found: list[Path] = []
    for child in _iter_dirs(watch_path):
        if case_folder_matches(child.name, case_folder_regex):
            found.append(child)
        if not recursive:
            continue
        for grandchild in _iter_dirs(child):
            if case_folder_matches(grandchild.name, case_folder_regex):
                found.append(grandchild)
    return found


def _folder_has_report(case_dir: Path) -> bool:
    try:
        return (case_dir / "report.html").is_file()
    except OSError:
        return False


def _newest_trigger_mtime(case_dir: Path, file_patterns: list[str]) -> float | None:
    newest: float | None = None
    try:
        with os.scandir(case_dir) as it:
            for ent in it:
                try:
                    if not ent.is_file(follow_symlinks=False):
                        continue
                    if not file_matches_patterns(ent.name, file_patterns):
                        continue
                    mtime = float(ent.stat().st_mtime)
                except OSError:
                    continue
                if newest is None or mtime > newest:
                    newest = mtime
    except OSError:
        return None
    return newest


def scan_unprocessed_cases(
    watch_path: Path,
    *,
    file_patterns: list[str],
    case_folder_regex: str,
    recursive: bool = True,
    min_age_sec: float = 0.0,
    now: float | None = None,
) -> list[Path]:
    """Case folders with a trigger file and no report.html (backup if events were missed)."""
    watch_path = Path(watch_path)
    now = time.time() if now is None else now
    out: list[Path] = []
    for case_dir in candidate_case_dirs(
        watch_path, recursive=recursive, case_folder_regex=case_folder_regex
    ):
        if _folder_has_report(case_dir):
            continue
        newest = _newest_trigger_mtime(case_dir, file_patterns)
        if newest is None:
            continue
        if min_age_sec > 0 and (now - newest) < min_age_sec:
            continue
        out.append(case_dir)
    return out


def default_watch_rules() -> dict:
    return {
        "watch_subfolders": DEFAULT_WATCH_RECURSIVE,
        "new_case_file_patterns": list(DEFAULT_WATCH_FILE_PATTERNS),
        "case_folder_name_regex": DEFAULT_CASE_FOLDER_REGEX,
        "machine_to_case_dir_levels": DEFAULT_WATCH_CASE_DIR_LEVELS,
        "queued_case_poll_sec": DEFAULT_WATCH_POLL_SEC,
        "disk_scan_for_new_case_detection": DEFAULT_WATCH_DISK_SCAN,
        "disk_scan_for_new_case_detection_sec": DEFAULT_WATCH_DISK_SCAN_SEC,
        "archive_old_cases": DEFAULT_WATCH_ARCHIVE,
        "archive_cases_older_than_days": DEFAULT_WATCH_ARCHIVE_AGE_DAYS,
        "archive_old_cases_at": DEFAULT_WATCH_ARCHIVE_AT,
    }


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        queue: list[str],
        lock: threading.Lock,
        *,
        file_patterns: list[str],
        case_folder_regex: str,
        case_dir_levels: int,
        busy: set[str] | None = None,
    ):
        self.queue = queue
        self.lock = lock
        self.busy = busy if busy is not None else set()
        self.file_patterns = file_patterns
        self.case_folder_regex = case_folder_regex
        self.case_dir_levels = case_dir_levels

    def _maybe_queue(self, path: str) -> None:
        file_path = Path(path)
        if not file_matches_patterns(file_path.name, self.file_patterns):
            return
        case_dir = case_dir_from_file(file_path, self.case_dir_levels)
        if not case_folder_matches(case_dir.name, self.case_folder_regex):
            logger.info("Skipping (case folder name): %s", case_dir)
            return
        case_key = str(case_dir)
        with self.lock:
            if case_key in self.queue or case_key in self.busy:
                logger.info("Queue already has this case....so skipping... %s", case_key)
                return
            logger.info("Adding to the queue... %s", case_key)
            self.queue.append(case_key)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_queue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe_queue(event.dest_path)


def watch(
    watch_path: str | Path,
    data_root: str | Path,
    poll_sec: float | None = None,
    stop_event: threading.Event | None = None,
    watcher: dict | None = None,
) -> None:
    watch_path = Path(watch_path)
    data_root = Path(data_root)
    rules = {**default_watch_rules(), **promote_watcher_aliases(watcher or {})}
    recursive = bool(rules.get("watch_subfolders", DEFAULT_WATCH_RECURSIVE))
    file_patterns = list(rules.get("new_case_file_patterns") or DEFAULT_WATCH_FILE_PATTERNS)
    case_folder_regex = str(rules.get("case_folder_name_regex") or "")
    try:
        case_dir_levels = int(
            rules.get("machine_to_case_dir_levels") or DEFAULT_WATCH_CASE_DIR_LEVELS
        )
    except (TypeError, ValueError):
        case_dir_levels = DEFAULT_WATCH_CASE_DIR_LEVELS
    if poll_sec is None:
        try:
            poll_sec = float(rules.get("queued_case_poll_sec") or DEFAULT_WATCH_POLL_SEC)
        except (TypeError, ValueError):
            poll_sec = DEFAULT_WATCH_POLL_SEC
    if poll_sec <= 0:
        poll_sec = DEFAULT_WATCH_POLL_SEC
    try:
        disk_scan_sec = float(
            rules.get("disk_scan_for_new_case_detection_sec", DEFAULT_WATCH_DISK_SCAN_SEC)
        )
    except (TypeError, ValueError):
        disk_scan_sec = DEFAULT_WATCH_DISK_SCAN_SEC
    if disk_scan_sec <= 0:
        disk_scan_sec = DEFAULT_WATCH_DISK_SCAN_SEC
    disk_scan = _as_bool(
        rules.get("disk_scan_for_new_case_detection"), DEFAULT_WATCH_DISK_SCAN
    )
    archive_enabled = _as_bool(rules.get("archive_old_cases"), DEFAULT_WATCH_ARCHIVE)
    try:
        archive_age_days = int(
            rules.get("archive_cases_older_than_days") or DEFAULT_WATCH_ARCHIVE_AGE_DAYS
        )
    except (TypeError, ValueError):
        archive_age_days = DEFAULT_WATCH_ARCHIVE_AGE_DAYS
    if archive_age_days < 1:
        archive_age_days = DEFAULT_WATCH_ARCHIVE_AGE_DAYS
    archive_at = parse_archive_at(
        rules.get("archive_old_cases_at") or DEFAULT_WATCH_ARCHIVE_AT
    )
    ensure_watch_folders(watch_path, data_root)
    queue: list[str] = []
    busy: set[str] = set()
    lock = threading.Lock()
    handler = _Handler(
        queue,
        lock,
        file_patterns=file_patterns,
        case_folder_regex=case_folder_regex,
        case_dir_levels=case_dir_levels,
        busy=busy,
    )
    observer = Observer()
    started = False
    try:
        observer.schedule(handler, str(watch_path), recursive=recursive)
        observer.start()
    except OSError as exc:
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception:
            pass
        _notify_unavailable(
            f"watch_path is not available: {watch_path} ({exc})",
            traceback.format_exc(),
        )
    started = True
    disk_min_age = max(15.0, float(poll_sec))
    archive_at_text = f"{archive_at.hour:02d}:{archive_at.minute:02d}"
    logger.info(
        "Watcher started on %s watch_subfolders=%s new_case_file_patterns=%s "
        "case_folder_name_regex=%s machine_to_case_dir_levels=%s "
        "queued_case_poll_sec=%.1f disk_scan_for_new_case_detection=%s "
        "disk_scan_for_new_case_detection_sec=%.1f disk_scan_min_age=%.1fs "
        "archive_old_cases=%s archive_cases_older_than_days=%s archive_old_cases_at=%s",
        watch_path,
        recursive,
        file_patterns,
        case_folder_regex or "(any)",
        case_dir_levels,
        poll_sec,
        disk_scan,
        disk_scan_sec,
        disk_min_age,
        archive_enabled,
        archive_age_days,
        archive_at_text,
    )
    details = (
        f"watch_path={watch_path}\n"
        f"winstonlutz_data_root={data_root}\n"
        f"watch_subfolders={recursive}\n"
        f"new_case_file_patterns={file_patterns}\n"
        f"case_folder_name_regex={case_folder_regex or '(any)'}\n"
        f"machine_to_case_dir_levels={case_dir_levels}\n"
        f"queued_case_poll_sec={poll_sec}\n"
        f"disk_scan_for_new_case_detection={disk_scan}\n"
        f"disk_scan_for_new_case_detection_sec={disk_scan_sec}\n"
        f"disk_scan_min_age={disk_min_age}\n"
        f"archive_old_cases={archive_enabled}\n"
        f"archive_cases_older_than_days={archive_age_days}\n"
        f"archive_old_cases_at={archive_at_text}"
    )
    send_event_email("Watcher started", details, context="watch-start", blocking=True)
    stop_event = stop_event or threading.Event()

    def _run_case(case_key: str) -> None:
        try:
            process_case(case_key, data_root)
        finally:
            with lock:
                busy.discard(case_key)

    now = time.monotonic()
    next_dequeue = now + poll_sec
    next_scan = now + disk_scan_sec if disk_scan else float("inf")
    archive_check_sec = 60.0
    next_archive_check = now + 15.0 if archive_enabled else float("inf")
    last_archive_date = None
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            wait = min(next_dequeue, next_scan, next_archive_check) - now
            if wait > 0 and stop_event.wait(wait):
                break
            if stop_event.is_set():
                break
            now = time.monotonic()
            if archive_enabled and now >= next_archive_check:
                next_archive_check = now + archive_check_sec
                wall = datetime.now()
                due = next_archive_datetime(archive_at, wall)
                if last_archive_date != wall.date() and wall >= due:
                    with lock:
                        skip = set(queue) | set(busy)
                    try:
                        result = archive_old_cases(
                            get_machines(),
                            age_days=archive_age_days,
                            now=wall,
                            skip_keys=skip,
                            case_folder_regex=case_folder_regex,
                        )
                    except Exception:
                        logger.exception("archive failed")
                        send_error_email(
                            "Watcher archive failed",
                            traceback.format_exc(),
                            context="watch-archive",
                            blocking=False,
                        )
                    else:
                        last_archive_date = wall.date()
                        logger.info("Archive finished: %s", result.summary())
                        if result.errors:
                            send_error_email(
                                f"Watcher archive errors ({len(result.errors)})",
                                result.summary(),
                                context="watch-archive",
                                blocking=False,
                            )
                        if result.moved:
                            send_event_email(
                                f"Watcher archived {len(result.moved)} case(s)",
                                result.summary(),
                                context="watch-archive",
                                blocking=False,
                            )
            if disk_scan and now >= next_scan:
                try:
                    missed = scan_unprocessed_cases(
                        watch_path,
                        file_patterns=file_patterns,
                        case_folder_regex=case_folder_regex,
                        recursive=recursive,
                        min_age_sec=disk_min_age,
                    )
                except Exception:
                    logger.exception("disk scan failed for %s", watch_path)
                    missed = []
                for missed_dir in missed:
                    case_key = str(missed_dir)
                    with lock:
                        if case_key in queue or case_key in busy:
                            continue
                        logger.info("Disk scan queued missed case... %s", case_key)
                        queue.append(case_key)
                next_scan = now + disk_scan_sec
            if now >= next_dequeue:
                case_dir = None
                with lock:
                    if queue:
                        case_dir = queue.pop(0)
                        busy.add(case_dir)
                if case_dir:
                    logger.info("Running case... %s", case_dir)
                    thread = threading.Thread(
                        target=_run_case,
                        args=(case_dir,),
                        daemon=True,
                    )
                    thread.start()
                next_dequeue = now + poll_sec
    except KeyboardInterrupt:
        logger.info("Watcher stopping")
    finally:
        if started:
            send_event_email(
                "Watcher stopped",
                details,
                context="watch-stop",
                blocking=True,
            )
        observer.stop()
        observer.join(timeout=5)
