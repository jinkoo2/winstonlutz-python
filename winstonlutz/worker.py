"""Per-case worker matching WinstonLutz_Worker."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from pathlib import Path

from .config import Param
from .emailer import send_from_config
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
        run_case(case_dir, data_root=data_root)
        log_file.write_text(f"Worker - Done. case={case_dir}\n", encoding="utf-8")
    except Exception as exc:
        logger.exception("Worker failed for %s", case_dir)
        try:
            log_file.write_text(f"Worker - Exception: {exc}\n", encoding="utf-8")
            app_cfg = data_root / "app.config.txt"
            if app_cfg.is_file():
                send_from_config(Param(app_cfg), "IGRT Directory Watcher Notify", str(exc), "email_status_notification_to")
        except Exception:
            logger.exception("Worker notify failed")
