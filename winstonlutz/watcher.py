"""Watch a transfer folder for RE.*.dcm and queue case directories."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .worker import process_case

logger = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: list[str], lock: threading.Lock):
        self.queue = queue
        self.lock = lock

    def _maybe_queue(self, path: str) -> None:
        name = Path(path).name
        if not name.startswith("RE.") or not name.lower().endswith(".dcm"):
            return
        case_dir = str(Path(path).parent)
        with self.lock:
            if case_dir in self.queue:
                logger.info("Queue already has this case....so skipping... %s", case_dir)
                return
            logger.info("Adding to the queue... %s", case_dir)
            self.queue.append(case_dir)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_queue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe_queue(event.dest_path)


def watch(
    watch_path: str | Path,
    data_root: str | Path,
    poll_sec: float = 10.0,
    stop_event: threading.Event | None = None,
) -> None:
    watch_path = Path(watch_path)
    data_root = Path(data_root)
    queue: list[str] = []
    lock = threading.Lock()
    handler = _Handler(queue, lock)
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()
    logger.info("Watcher started on %s", watch_path)
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
    finally:
        observer.stop()
        observer.join(timeout=5)
