from winstonlutz.watcher import (
    WatchPathUnavailable,
    case_dir_from_file,
    case_folder_matches,
    ensure_watch_folders,
    file_matches_patterns,
    folder_unavailable_reason,
)


def test_folder_unavailable_reason_missing(tmp_path):
    missing = tmp_path / "nope"
    msg = folder_unavailable_reason(missing, "watch_path")
    assert msg is not None
    assert "watch_path" in msg
    assert str(missing) in msg


def test_folder_unavailable_reason_ok(tmp_path):
    assert folder_unavailable_reason(tmp_path, "watch_path") is None


def test_folder_unavailable_reason_file_not_dir(tmp_path):
    file = tmp_path / "not_a_dir"
    file.write_text("x", encoding="utf-8")
    msg = folder_unavailable_reason(file, "winstonlutz_data_root")
    assert msg is not None
    assert "not a directory" in msg


def test_ensure_watch_folders_emails_and_raises(tmp_path, monkeypatch):
    sent = []

    def fake_send(message, details="", *, context="", blocking=False):
        sent.append({"message": message, "context": context, "blocking": blocking})

    monkeypatch.setattr("winstonlutz.watcher.send_error_email", fake_send)
    missing = tmp_path / "offline_share"
    try:
        ensure_watch_folders(missing, tmp_path)
        assert False
    except WatchPathUnavailable as exc:
        assert "watch_path" in str(exc)
    assert len(sent) == 1
    assert sent[0]["context"] == "watch"
    assert sent[0]["blocking"] is True
    assert "watch_path" in sent[0]["message"]


def test_cli_watch_missing_path_exits_without_traceback(tmp_path, monkeypatch, capsys):
    sent = []

    def fake_send(message, details="", *, context="", blocking=False):
        sent.append(message)

    monkeypatch.setattr("winstonlutz.watcher.send_error_email", fake_send)
    from winstonlutz.cli import main

    code = main(
        [
            "watch",
            "--watch-path",
            str(tmp_path / "missing_watch"),
            "--data-root",
            str(tmp_path),
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "watch_path was not found" in err
    assert "Traceback" not in err
    assert sent


def test_watch_start_oserror_emails(tmp_path, monkeypatch):
    sent = []

    def fake_send(message, details="", *, context="", blocking=False):
        sent.append({"message": message, "details": details, "blocking": blocking})

    class BoomObserver:
        def schedule(self, *args, **kwargs):
            return None

        def start(self):
            raise FileNotFoundError(53, "The network path was not found")

        def stop(self):
            return None

        def join(self, timeout=None):
            return None

    monkeypatch.setattr("winstonlutz.watcher.send_error_email", fake_send)
    monkeypatch.setattr("winstonlutz.watcher.Observer", BoomObserver)
    from winstonlutz.watcher import watch

    try:
        watch(tmp_path, tmp_path, poll_sec=0.01)
        assert False
    except WatchPathUnavailable as exc:
        assert "not available" in str(exc)
    assert sent
    assert sent[0]["blocking"] is True
    assert "FileNotFoundError" in sent[0]["details"] or "network path" in sent[0]["message"]


def test_watch_start_stop_sends_event_emails(tmp_path, monkeypatch):
    import threading

    events = []

    def fake_event(message, details="", *, context="", blocking=False):
        events.append({"message": message, "context": context, "blocking": blocking})

    monkeypatch.setattr("winstonlutz.watcher.send_event_email", fake_event)
    from winstonlutz.watcher import watch

    stop = threading.Event()
    stop.set()
    watch(tmp_path, tmp_path, poll_sec=0.01, stop_event=stop)
    assert [item["message"] for item in events] == ["Watcher started", "Watcher stopped"]
    assert events[0]["context"] == "watch-start"
    assert events[1]["context"] == "watch-stop"
    assert events[0]["blocking"] is True
    assert events[1]["blocking"] is True


def test_file_and_case_folder_rules(tmp_path):
    from winstonlutz.app_settings import DEFAULT_CASE_FOLDER_REGEX, watcher_settings
    from winstonlutz.watcher import _Handler

    assert file_matches_patterns("RE.1.2.3.dcm", ["RE.*.dcm"])
    assert not file_matches_patterns("RI.1.2.3.dcm", ["RE.*.dcm"])
    assert file_matches_patterns("ready.dcm", ["*.dcm"])
    assert case_folder_matches("26-09-23_06-21-08", DEFAULT_CASE_FOLDER_REGEX)
    assert not case_folder_matches("scratch", DEFAULT_CASE_FOLDER_REGEX)
    assert case_folder_matches("scratch", "")
    nested = tmp_path / "Edge" / "26-09-23_06-21-08" / "extra" / "RE.1.dcm"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"")
    assert case_dir_from_file(nested, 1) == nested.parent
    assert case_dir_from_file(nested, 2) == nested.parent.parent

    cfg = watcher_settings(
        {
            "Watcher": {
                "watch_path": r"\\share\QA",
                "winstonlutz_data_root": r"\\share\WL",
                "new_case_file_patterns": "RE.*.dcm, *.ready",
                "watch_subfolders": False,
                "case_folder_name_regex": "",
                "machine_to_case_dir_levels": 2,
                "queued_case_poll_sec": 5,
                "disk_scan_for_new_case_detection": False,
                "disk_scan_for_new_case_detection_sec": 120,
                "archive_old_cases": False,
                "archive_cases_older_than_days": 14,
                "archive_old_cases_at": "02:30",
            }
        }
    )
    assert cfg["watch_subfolders"] is False
    assert cfg["new_case_file_patterns"] == ["RE.*.dcm", "*.ready"]
    assert cfg["case_folder_name_regex"] == ""
    assert cfg["machine_to_case_dir_levels"] == 2
    assert cfg["queued_case_poll_sec"] == 5
    assert cfg["disk_scan_for_new_case_detection"] is False
    assert cfg["disk_scan_for_new_case_detection_sec"] == 120
    assert cfg["archive_old_cases"] is False
    assert cfg["archive_cases_older_than_days"] == 14
    assert cfg["archive_old_cases_at"] == "02:30"

    missing = watcher_settings({"Watcher": {"watch_path": "x", "winstonlutz_data_root": "y"}})
    assert missing["new_case_file_patterns"] == ["RE.*.dcm"]
    assert missing["watch_subfolders"] is True
    assert missing["case_folder_name_regex"] == DEFAULT_CASE_FOLDER_REGEX
    assert missing["disk_scan_for_new_case_detection"] is True
    assert missing["disk_scan_for_new_case_detection_sec"] == 60
    assert missing["archive_old_cases"] is True
    assert missing["archive_cases_older_than_days"] == 7
    assert missing["archive_old_cases_at"] == "01:00"

    legacy = watcher_settings(
        {
            "Watcher": {
                "watch_path": "x",
                "data_root": "y",
                "recursive": False,
                "file_patterns": ["RE.*.dcm"],
                "case_folder_regex": "",
                "case_dir_levels": 2,
                "poll_sec": 5,
                "disk_scan": False,
                "disk_scan_sec": 120,
                "archive": False,
                "archive_age_days": 14,
                "archive_at": "02:30",
            }
        }
    )
    assert legacy["winstonlutz_data_root"] == "y"
    assert legacy["watch_subfolders"] is False
    assert legacy["machine_to_case_dir_levels"] == 2
    assert legacy["queued_case_poll_sec"] == 5
    assert legacy["disk_scan_for_new_case_detection"] is False
    assert legacy["archive_old_cases"] is False
    assert legacy["archive_cases_older_than_days"] == 14
    assert legacy["archive_old_cases_at"] == "02:30"

    queue: list[str] = []
    import threading

    handler = _Handler(
        queue,
        threading.Lock(),
        file_patterns=["RE.*.dcm"],
        case_folder_regex=DEFAULT_CASE_FOLDER_REGEX,
        case_dir_levels=1,
    )
    case = tmp_path / "26-09-29_16-00-00"
    case.mkdir()
    handler._maybe_queue(str(case / "RI.1.dcm"))
    assert queue == []
    handler._maybe_queue(str(case / "RE.1.dcm"))
    assert queue == [str(case)]
    handler._maybe_queue(str(case / "RE.2.dcm"))
    assert queue == [str(case)]
    other = tmp_path / "not-a-case"
    other.mkdir()
    handler._maybe_queue(str(other / "RE.1.dcm"))
    assert queue == [str(case)]

    busy = {str(case)}
    queue.clear()
    handler.busy = busy
    handler._maybe_queue(str(case / "RE.3.dcm"))
    assert queue == []


def test_scan_unprocessed_cases_skips_report_and_young_files(tmp_path):
    from winstonlutz.app_settings import DEFAULT_CASE_FOLDER_REGEX
    from winstonlutz.watcher import scan_unprocessed_cases

    edge = tmp_path / "Edge"
    missed = edge / "26-09-30_06-27-37"
    done = edge / "26-09-29_06-00-00"
    young = edge / "26-09-30_07-00-00"
    scratch = edge / "scratch"
    missed.mkdir(parents=True)
    done.mkdir(parents=True)
    young.mkdir(parents=True)
    scratch.mkdir()
    (missed / "RE.1.dcm").write_bytes(b"x")
    (done / "RE.1.dcm").write_bytes(b"x")
    (done / "report.html").write_text("<html></html>", encoding="utf-8")
    (young / "RE.1.dcm").write_bytes(b"x")
    (scratch / "RE.1.dcm").write_bytes(b"x")
    now = 1_000_000.0
    import os

    os.utime(missed / "RE.1.dcm", (now - 60, now - 60))
    os.utime(young / "RE.1.dcm", (now - 5, now - 5))
    found = scan_unprocessed_cases(
        tmp_path,
        file_patterns=["RE.*.dcm"],
        case_folder_regex=DEFAULT_CASE_FOLDER_REGEX,
        recursive=True,
        min_age_sec=15,
        now=now,
    )
    assert found == [missed]


def test_scan_unprocessed_cases_direct_child(tmp_path):
    from winstonlutz.app_settings import DEFAULT_CASE_FOLDER_REGEX
    from winstonlutz.watcher import scan_unprocessed_cases

    case = tmp_path / "26-09-30_06-27-37"
    case.mkdir()
    (case / "RE.1.dcm").write_bytes(b"x")
    found = scan_unprocessed_cases(
        tmp_path,
        file_patterns=["RE.*.dcm"],
        case_folder_regex=DEFAULT_CASE_FOLDER_REGEX,
        recursive=False,
        min_age_sec=0,
    )
    assert found == [case]
