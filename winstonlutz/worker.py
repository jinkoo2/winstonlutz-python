"""Per-case worker matching WinstonLutz_Worker."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from pathlib import Path

from .emailer import send_error_email
from .pipeline import run_case

logger = logging.getLogger(__name__)


def process_case(
    case_dir: str | Path,
    data_root: str | Path,
    delay_sec: tuple[float, float] = (1.0, 3.0),
) -> None:
    case_dir = Path(case_dir)
    data_root = Path(data_root)
    wait = random.uniform(*delay_sec)
    logger.info("Worker - Waiting %.1f seconds...", wait)
    time.sleep(wait)

    now = datetime.now()
    log_name = now.strftime("log_%Y%m%d_%H%M%S") + f"{int(now.microsecond / 1000):03d}.txt"
    log_file = case_dir / log_name
    try:
        try:
            from .app_settings import find_machine_by_name, simple_machine_name
            from .case_notify import notify_new_qa_case

            name = simple_machine_name(case_dir)
            machine = find_machine_by_name(name) or {}
            notify_new_qa_case(str(machine.get("NAME") or name), case_dir)
        except Exception:
            logger.exception("new-case notify failed for %s", case_dir)
        run_case(case_dir, data_root=data_root, send_email=True)
        log_file.write_text(f"Worker - Done. case={case_dir}\n", encoding="utf-8")
    except Exception as exc:
        logger.exception("Worker failed for %s", case_dir)
        try:
            log_file.write_text(f"Worker - Exception: {exc}\n", encoding="utf-8")
            send_error_email(str(exc) or "watcher failed", context="watcher", blocking=True)
        except Exception:
            logger.exception("Worker notify failed")
