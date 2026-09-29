"""Watch a transfer folder for configured trigger files and queue case directories."""

from __future__ import annotations

import fnmatch
import logging
import re
import threading
import time
import traceback
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .app_settings import (
    DEFAULT_CASE_FOLDER_REGEX,
    DEFAULT_WATCH_CASE_DIR_LEVELS,
    DEFAULT_WATCH_FILE_PATTERNS,
    DEFAULT_WATCH_POLL_SEC,
    DEFAULT_WATCH_RECURSIVE,
)
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
    for path, label in ((watch_path, "watch_path"), (data_root, "data_root")):
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


def default_watch_rules() -> dict:
    return {
        "recursive": DEFAULT_WATCH_RECURSIVE,
        "file_patterns": list(DEFAULT_WATCH_FILE_PATTERNS),
        "case_folder_regex": DEFAULT_CASE_FOLDER_REGEX,
        "case_dir_levels": DEFAULT_WATCH_CASE_DIR_LEVELS,
        "poll_sec": DEFAULT_WATCH_POLL_SEC,
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
    ):
        self.queue = queue
        self.lock = lock
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
            if case_key in self.queue:
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
    rules = {**default_watch_rules(), **(watcher or {})}
    recursive = bool(rules.get("recursive", DEFAULT_WATCH_RECURSIVE))
    file_patterns = list(rules.get("file_patterns") or DEFAULT_WATCH_FILE_PATTERNS)
    case_folder_regex = str(rules.get("case_folder_regex") or "")
    try:
        case_dir_levels = int(rules.get("case_dir_levels") or DEFAULT_WATCH_CASE_DIR_LEVELS)
    except (TypeError, ValueError):
        case_dir_levels = DEFAULT_WATCH_CASE_DIR_LEVELS
    if poll_sec is None:
        try:
            poll_sec = float(rules.get("poll_sec") or DEFAULT_WATCH_POLL_SEC)
        except (TypeError, ValueError):
            poll_sec = DEFAULT_WATCH_POLL_SEC
    ensure_watch_folders(watch_path, data_root)
    queue: list[str] = []
    lock = threading.Lock()
    handler = _Handler(
        queue,
        lock,
        file_patterns=file_patterns,
        case_folder_regex=case_folder_regex,
        case_dir_levels=case_dir_levels,
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
    logger.info(
        "Watcher started on %s recursive=%s files=%s case_folder_regex=%s case_dir_levels=%s",
        watch_path,
        recursive,
        file_patterns,
        case_folder_regex or "(any)",
        case_dir_levels,
    )
    details = (
        f"watch_path={watch_path}\n"
        f"data_root={data_root}\n"
        f"recursive={recursive}\n"
        f"file_patterns={file_patterns}\n"
        f"case_folder_regex={case_folder_regex or '(any)'}\n"
        f"case_dir_levels={case_dir_levels}"
    )
    send_event_email("Watcher started", details, context="watch-start", blocking=True)
    stop_event = stop_event or threading.Event()
    try:
        while not stop_event.is_set():
            time.sleep(poll_sec)
            case_dir = None
            with lock:
                if queue:
                    case_dir = queue.pop(0)
            if case_dir:
                logger.info("Running case... %s", case_dir)
                thread = threading.Thread(
                    target=process_case,
                    args=(case_dir, data_root),
                    daemon=True,
                )
                thread.start()
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
