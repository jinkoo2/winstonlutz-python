from datetime import datetime, time
from pathlib import Path

from winstonlutz.archive import (
    archive_old_cases,
    case_is_older_than,
    format_archive_at,
    next_archive_datetime,
    parse_archive_at,
)


def test_parse_and_format_archive_at():
    assert parse_archive_at("1:00") == time(1, 0)
    assert parse_archive_at("01:15") == time(1, 15)
    assert parse_archive_at("01:15:30") == time(1, 15)
    assert parse_archive_at("nope") == time(1, 0)
    assert format_archive_at("9:05") == "09:05"


def test_next_archive_datetime_window():
    run_at = time(1, 0)
    before = datetime(2026, 9, 30, 0, 30)
    assert next_archive_datetime(run_at, before) == datetime(2026, 9, 30, 1, 0)
    in_window = datetime(2026, 9, 30, 1, 15)
    due = next_archive_datetime(run_at, in_window)
    assert due == in_window.replace(microsecond=0)
    daytime = datetime(2026, 9, 30, 8, 0)
    assert next_archive_datetime(run_at, daytime) == datetime(2026, 10, 1, 1, 0)
    after_window = datetime(2026, 9, 30, 4, 0)
    assert next_archive_datetime(run_at, after_window) == datetime(2026, 10, 1, 1, 0)


def test_case_is_older_than_uses_folder_date():
    now = datetime(2026, 9, 30, 1, 0)
    old = Path("26-09-23_06-21-08")
    young = Path("26-09-29_06-00-00")
    assert case_is_older_than(old, 7, now=now)
    assert not case_is_older_than(young, 7, now=now)
    assert case_is_older_than(old, 7, now=datetime(2026, 9, 30, 1, 0))
    assert not case_is_older_than(Path("26-09-23_06-21-08"), 8, now=now)


def test_archive_old_cases_moves_watch_to_data(tmp_path):
    watch = tmp_path / "watch"
    data = tmp_path / "data"
    watch.mkdir()
    data.mkdir()
    old = watch / "26-09-20_06-00-00"
    young = watch / "26-09-29_08-00-00"
    old.mkdir()
    (old / "report.html").write_text("old", encoding="utf-8")
    young.mkdir()
    (young / "report.html").write_text("young", encoding="utf-8")
    machines = [
        {
            "NAME": "Edge",
            "WATCH_FOLDER": str(watch),
            "DATA_FOLDER": str(data),
        }
    ]
    now = datetime(2026, 9, 30, 1, 0)
    result = archive_old_cases(machines, age_days=7, now=now)
    assert [Path(src).name for src, _dest in result.moved] == ["26-09-20_06-00-00"]
    assert not old.exists()
    assert (data / "26-09-20_06-00-00" / "report.html").is_file()
    assert young.exists()
    assert not (data / "26-09-29_08-00-00").exists()


def test_archive_skips_existing_dest_and_in_use(tmp_path):
    watch = tmp_path / "watch"
    data = tmp_path / "data"
    watch.mkdir()
    data.mkdir()
    duplicate = watch / "26-09-20_06-00-00"
    busy = watch / "26-09-19_06-00-00"
    duplicate.mkdir()
    busy.mkdir()
    (data / "26-09-20_06-00-00").mkdir()
    machines = [
        {
            "NAME": "Edge",
            "WATCH_FOLDER": str(watch),
            "DATA_FOLDER": str(data),
        }
    ]
    now = datetime(2026, 9, 30, 1, 0)
    result = archive_old_cases(
        machines, age_days=7, now=now, skip_keys={str(busy)}
    )
    reasons = {Path(path).name: reason for path, reason in result.skipped}
    assert "already in DATA_FOLDER" in reasons["26-09-20_06-00-00"]
    assert reasons["26-09-19_06-00-00"] == "in use"
    assert duplicate.exists()
    assert busy.exists()
    assert result.moved == []


def test_archive_dry_run_does_not_move(tmp_path):
    watch = tmp_path / "watch"
    data = tmp_path / "data"
    watch.mkdir()
    data.mkdir()
    old = watch / "26-09-20_06-00-00"
    old.mkdir()
    machines = [{"NAME": "Edge", "WATCH_FOLDER": str(watch), "DATA_FOLDER": str(data)}]
    result = archive_old_cases(
        machines, age_days=7, now=datetime(2026, 9, 30, 1, 0), dry_run=True
    )
    assert result.moved
    assert old.exists()
    assert not (data / old.name).exists()
