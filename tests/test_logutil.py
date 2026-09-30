from datetime import date, timedelta
from pathlib import Path


def test_prune_old_logs(tmp_path):
    from winstonlutz.logutil import LOG_NAME_PREFIX, LOG_NAME_SUFFIX, prune_old_logs

    today = date(2026, 9, 28)

    def log_file(day: date) -> Path:
        return tmp_path / f"{LOG_NAME_PREFIX}{day.isoformat()}{LOG_NAME_SUFFIX}"

    keep = log_file(today)
    edge = log_file(today - timedelta(days=7))
    old = log_file(today - timedelta(days=8))
    junk = tmp_path / "other.log"
    for path in (keep, edge, old, junk):
        path.write_text("x", encoding="utf-8")

    prune_old_logs(tmp_path, today=today)
    assert keep.is_file()
    assert edge.is_file()
    assert not old.is_file()
    assert junk.is_file()


def test_configure_logging_handles_none_stdio(tmp_path, monkeypatch):
    import logging
    import sys

    from winstonlutz import logutil

    monkeypatch.setattr(logutil, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logutil.configure_logging()
        logging.getLogger("winstonlutz.testlog").info("frozen-no-tty")
        for handler in root.handlers:
            handler.flush()
        path = tmp_path / "_logs" / f"winstonlutz_{date.today().isoformat()}.log"
        assert path.is_file()
        assert "frozen-no-tty" in path.read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass


def test_configure_logging_writes_daily_file(tmp_path, monkeypatch):
    import logging

    from winstonlutz import logutil

    monkeypatch.setattr(logutil, "app_dir", lambda: tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logutil.configure_logging(console=False)
        logger = logging.getLogger("winstonlutz.testlog")
        logger.info("hello-log")
        for handler in root.handlers:
            handler.flush()
        path = tmp_path / "_logs" / f"winstonlutz_{date.today().isoformat()}.log"
        assert path.is_file()
        assert "hello-log" in path.read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
