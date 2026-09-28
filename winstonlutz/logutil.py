"""App file logging next to the executable: ``_logs/winstonlutz_YYYY-MM-DD.log``."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .app_settings import app_dir

LOGS_DIR_NAME = "_logs"
LOG_NAME_PREFIX = "winstonlutz_"
LOG_NAME_SUFFIX = ".log"
KEEP_DAYS = 7
_FILE_HANDLER_MARK = "_winstonlutz_daily_log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def logs_dir() -> Path:
    return app_dir() / LOGS_DIR_NAME


def log_path_for(day: date | None = None) -> Path:
    day = day or date.today()
    return logs_dir() / f"{LOG_NAME_PREFIX}{day.isoformat()}{LOG_NAME_SUFFIX}"


def _parse_log_date(path: Path) -> date | None:
    name = path.name
    if not name.startswith(LOG_NAME_PREFIX) or not name.endswith(LOG_NAME_SUFFIX):
        return None
    stamp = name[len(LOG_NAME_PREFIX) : -len(LOG_NAME_SUFFIX)]
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def prune_old_logs(folder: Path, *, today: date | None = None) -> None:
    """Delete ``winstonlutz_YYYY-MM-DD.log`` files older than ``KEEP_DAYS``."""
    today = today or date.today()
    cutoff = today - timedelta(days=KEEP_DAYS)
    try:
        paths = list(folder.glob(f"{LOG_NAME_PREFIX}*{LOG_NAME_SUFFIX}"))
    except OSError:
        return
    for path in paths:
        if not path.is_file():
            continue
        parsed = _parse_log_date(path)
        if parsed is None or parsed >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _level_from_env() -> int | None:
    text = os.environ.get("WINSTONLUTZ_LOG_LEVEL", "").strip().upper()
    if not text:
        return None
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(text)


def _has_our_file_handler(root: logging.Logger) -> bool:
    return any(getattr(handler, _FILE_HANDLER_MARK, False) for handler in root.handlers)


def _has_stream_handler(root: logging.Logger) -> bool:
    for handler in root.handlers:
        if getattr(handler, _FILE_HANDLER_MARK, False):
            continue
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            return True
    return False


def configure_logging(*, verbose: bool = False, console: bool | None = None) -> None:
    """Attach a daily file handler under ``_logs``. Failures are ignored.

    ``console`` defaults to True unless this is a frozen app with no TTY.
    """
    env_level = _level_from_env()
    if verbose:
        level = logging.DEBUG
    elif env_level is not None:
        level = env_level
    else:
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(min(root.level, level) if root.handlers else level)
    if verbose:
        root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(_FORMAT)

    if console is None:
        frozen = bool(getattr(sys, "frozen", False))
        console = (not frozen) or sys.stderr.isatty()
    if console and not _has_stream_handler(root):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(level)
        root.addHandler(stream)

    if not _has_our_file_handler(root):
        try:
            folder = logs_dir()
            folder.mkdir(parents=True, exist_ok=True)
            prune_old_logs(folder)
            path = log_path_for()
            handler = logging.FileHandler(path, encoding="utf-8")
            setattr(handler, _FILE_HANDLER_MARK, True)
            handler.setFormatter(formatter)
            handler.setLevel(level)
            root.addHandler(handler)
        except Exception:
            pass
    try:
        from .emailer import install_error_email_hooks

        install_error_email_hooks()
    except Exception:
        pass
