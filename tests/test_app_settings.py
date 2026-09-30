from pathlib import Path

from winstonlutz.app_settings import (
    LEGACY_SETTINGS_NAME,
    SETTINGS_NAME,
    app_dir,
    load_gui_settings,
    save_gui_settings,
    user_config_path,
)
from winstonlutz.config import Param


def test_param_set_value_preserves_comments(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text("# keep me\nFOO=old\nBAR=1\n", encoding="utf-8")
    Param(cfg).set_value("FOO", "new")
    text = cfg.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "FOO=new" in text
    assert "BAR=1" in text


def test_config_lives_next_to_app(monkeypatch):
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    path = user_config_path()
    assert path.name == SETTINGS_NAME
    assert path.parent == app_dir()
    assert "Roaming" not in path.parts


def test_legacy_settings_name_is_read_then_save_uses_settings_json(tmp_path, monkeypatch):
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    monkeypatch.setattr("winstonlutz.app_settings.app_dir", lambda: tmp_path)
    legacy = tmp_path / LEGACY_SETTINGS_NAME
    legacy.write_text('{"Institution": "Legacy Clinic"}\n', encoding="utf-8")
    assert user_config_path().name == LEGACY_SETTINGS_NAME
    assert load_gui_settings()["Institution"] == "Legacy Clinic"
    save_gui_settings({"Institution": "New Clinic"})
    assert (tmp_path / SETTINGS_NAME).is_file()
    assert load_gui_settings()["Institution"] == "New Clinic"


def test_strip_jsonc_and_load_settings(tmp_path, monkeypatch):
    from winstonlutz.app_settings import load_gui_settings, strip_jsonc

    raw = """
    // header
    {
      "Institution": "Test",
      "url": "https://example.com/path", // keep the URL
      "path": "C:\\\\share\\\\QA",
      /* block
         comment */
      "Watcher": { "queued_case_poll_sec": 10 }
    }
    """
    stripped = strip_jsonc(raw)
    assert "header" not in stripped
    assert "block" not in stripped
    assert "https://example.com/path" in stripped
    cfg = tmp_path / SETTINGS_NAME
    cfg.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(cfg))
    data = load_gui_settings()
    assert data["Institution"] == "Test"
    assert data["url"] == "https://example.com/path"
    assert data["Watcher"]["queued_case_poll_sec"] == 10


def test_sample_settings_jsonc_loads():
    import json
    from pathlib import Path

    from winstonlutz.app_settings import strip_jsonc

    sample = Path(__file__).resolve().parents[1] / "settings.sample.json"
    data = json.loads(strip_jsonc(sample.read_text(encoding="utf-8")))
    assert data["Watcher"]["archive_old_cases"] is True
    assert data["MACHINES"][0]["NAME"] == "Edge"


def test_find_machine_for_folder(tmp_path, monkeypatch):
    from winstonlutz.app_settings import find_machine_for_folder, save_gui_settings
    from winstonlutz.pipeline import validate_case_dir_name

    cfg = tmp_path / SETTINGS_NAME
    data = tmp_path / "Edge" / "Data"
    case = data / "26-09-25_06-15-37"
    case.mkdir(parents=True)
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(cfg))
    save_gui_settings(
        {
            "MACHINES": [
                {
                    "NAME": "Edge",
                    "DATA_FOLDER": str(data),
                    "CASE_FOLDER_NAME_REGEX": r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$",
                    "MV_bb_search_method": "ConnectedComponent",
                    "WL_pass_tolerance": 1.0,
                }
            ]
        }
    )
    found = find_machine_for_folder(case)
    assert found is not None
    assert found["NAME"] == "Edge"
    validate_case_dir_name(case, found["CASE_FOLDER_NAME_REGEX"])
    try:
        validate_case_dir_name(data / "nope")
        assert False
    except ValueError:
        pass


def test_find_machine_for_watch_folder(tmp_path, monkeypatch):
    from winstonlutz.app_settings import find_machine_for_folder, save_gui_settings

    cfg = tmp_path / SETTINGS_NAME
    watch = tmp_path / "Edge"
    case = watch / "26-09-30_06-27-37"
    case.mkdir(parents=True)
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(cfg))
    save_gui_settings(
        {
            "MACHINES": [
                {
                    "NAME": "Edge",
                    "WATCH_FOLDER": str(watch),
                    "DATA_FOLDER": str(tmp_path / "archive" / "Edge" / "Data"),
                }
            ]
        }
    )
    found = find_machine_for_folder(case)
    assert found is not None
    assert found["NAME"] == "Edge"


def test_machine_name_is_case_parent(tmp_path, monkeypatch):
    from winstonlutz.app_settings import (
        find_machine_by_name,
        find_machine_for_folder,
        save_gui_settings,
        simple_machine_name,
    )

    case = tmp_path / "Edge" / "26-09-23_06-21-08"
    case.mkdir(parents=True)
    (case / "RE.1.dcm").write_bytes(b"")
    assert simple_machine_name(case) == "Edge"
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / SETTINGS_NAME))
    save_gui_settings({"MACHINES": [{"NAME": "Edge", "DATA_FOLDER": str(tmp_path / "other")}]})
    assert find_machine_by_name("Edge")["NAME"] == "Edge"
    assert find_machine_for_folder(case)["NAME"] == "Edge"


def test_html_cid_images(tmp_path):
    from winstonlutz.emailer import html_with_cid_images

    png = tmp_path / "RI.1.dcm_out" / "result.png"
    png.parent.mkdir()
    png.write_bytes(b"\x89PNG\r\n")
    html = '<img src=".\\RI.1.dcm_out\\result.png" width="200"/>'
    out, images = html_with_cid_images(html, tmp_path)
    assert "cid:wl0" in out
    assert images == [("wl0", png.resolve())]


def test_list_case_folders_skips_empty(tmp_path):
    from winstonlutz.pipeline import case_has_ri, list_case_candidates, list_case_folders

    data = tmp_path / "Data"
    empty = data / "26-09-23_06-21-08"
    empty.mkdir(parents=True)
    no_ri = data / "26-09-24_06-13-24"
    no_ri.mkdir()
    (no_ri / "notes.txt").write_text("x", encoding="utf-8")
    good = data / "26-09-25_06-15-37"
    good.mkdir()
    (good / "RI.1.2.3.dcm").write_bytes(b"")
    junk = data / "json"
    junk.mkdir()
    machine = {
        "DATA_FOLDER": str(data),
        "CASE_FOLDER_NAME_REGEX": r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$",
    }

    names = [p.name for p in list_case_candidates(machine)]
    assert names == ["26-09-25_06-15-37", "26-09-24_06-13-24", "26-09-23_06-21-08"]
    assert case_has_ri(good)
    assert not case_has_ri(empty)
    cases = list_case_folders(machine)
    assert [p.name for p in cases] == ["26-09-25_06-15-37"]


def test_list_case_candidates_watch_then_data(tmp_path):
    from winstonlutz.app_settings import machine_case_roots
    from winstonlutz.pipeline import list_case_candidates

    watch = tmp_path / "Watch"
    data = tmp_path / "Data"
    live = watch / "26-09-30_08-00-00"
    live.mkdir(parents=True)
    archived = data / "26-09-24_06-13-24"
    archived.mkdir(parents=True)
    duplicate = data / "26-09-30_08-00-00"
    duplicate.mkdir()
    machine = {
        "WATCH_FOLDER": str(watch),
        "DATA_FOLDER": str(data),
        "CASE_FOLDER_NAME_REGEX": r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$",
    }
    assert machine_case_roots(machine) == [watch, data]
    found = list_case_candidates(machine)
    assert [p.name for p in found] == ["26-09-30_08-00-00", "26-09-24_06-13-24"]
    assert found[0] == live


def test_case_open_status_from_report(tmp_path):
    from winstonlutz.pipeline import case_has_html_report, case_open_status

    case = tmp_path / "26-09-25_06-15-37"
    case.mkdir()
    assert case_open_status(case) == "new"
    assert not case_has_html_report(case)
    (case / "report.html").write_text(
        '<span class="input-group-addon" id="basic-addon1">Result</span>\n'
        '<input type="text" class="form-control" value="Fail">',
        encoding="utf-8",
    )
    assert case_has_html_report(case)
    assert case_open_status(case) == "fail"
    (case / "report.html").write_text(
        '<span class="input-group-addon" id="basic-addon1">Result</span>\n'
        '<input type="text" class="form-control" value="Pass">',
        encoding="utf-8",
    )
    assert case_open_status(case) == "pass"


def test_case_open_status_sample_report():
    from winstonlutz.pipeline import case_open_status

    sample = (
        Path(__file__).resolve().parent.parent
        / "sample_data"
        / "Edge"
        / "Data"
        / "26-09-25_06-15-37"
    )
    if not (sample / "report.html").is_file():
        return
    assert case_open_status(sample) == "pass"


def test_case_result_status_new(tmp_path):
    from winstonlutz.pipeline import case_result_status

    case = tmp_path / "26-09-25_06-15-37"
    case.mkdir()
    (case / "RI.1.dcm").write_bytes(b"")
    assert case_result_status(case) == "new"


def test_analysis_params_from_machine():
    from winstonlutz.analysis import AnalysisParams, analysis_params_from_machine

    assert analysis_params_from_machine(None) == AnalysisParams()
    assert analysis_params_from_machine({}) == AnalysisParams()
    params = analysis_params_from_machine(
        {
            "crop_mm": [40, 45],
            "sad_mm": 800,
            "default_sid_mm": 1600,
            "MV_kvp_min": 500,
            "MV_image_size": [1000, 1100],
            "kV_image_size": "512x384",
        }
    )
    assert params.crop_mm == 40
    assert params.crop_mm_y == 45
    assert params.sad_mm == 800
    assert params.default_sid_mm == 1600
    assert params.mv_kvp_min == 500
    assert params.mv_image_size == (1000, 1100)
    assert params.kv_image_size == (512, 384)
    params = analysis_params_from_machine(
        {
            "MV_field_search_method": "Otsu",
            "kV_field_search_method": "ImageCenter",
        }
    )
    assert params.mv_field_search_method == "Otsu"
    assert params.kv_field_search_method == "ImageCenter"
    from winstonlutz.analysis import use_field_mask

    assert use_field_mask("MV", params) is True
    assert use_field_mask("kV", params) is False
    params = analysis_params_from_machine(
        {
            "nominal_gantry_angles": [270, 0, 90, 180],
            "nominal_table_angles": "90, 45, 0, 315, 270",
        }
    )
    assert params.nominal_gantry_angles == (270.0, 0.0, 90.0, 180.0)
    assert params.nominal_table_angles == (90.0, 45.0, 0.0, 315.0, 270.0)
    assert params.nominal_collimator_angles == ()


def test_snap_angle_circular_and_list_order():
    from winstonlutz.analysis import angle_sort_key, display_angle, snap_angle, snap_index

    gantry = (270.0, 0.0, 90.0, 180.0)
    table = (90.0, 45.0, 0.0, 315.0, 270.0)
    coll = (135.0, 90.0, 45.0, 0.0, 315.0, 270.0, 225.0)

    assert snap_angle(1.5, gantry) == 0.0
    assert snap_angle(358.0, gantry) == 0.0
    assert snap_angle(269.2, gantry) == 270.0
    assert snap_angle(179.97, gantry) == 180.0
    assert snap_index(0.0, gantry) == 1
    assert snap_index(270.0, gantry) == 0
    assert angle_sort_key(90.0, gantry) == 2

    assert snap_angle(46.0, table) == 45.0
    assert snap_angle(308.8, table) == 315.0
    assert snap_angle(356.0, table) == 0.0

    assert snap_angle(2.0, coll) == 0.0
    assert snap_angle(130.0, coll) == 135.0
    assert display_angle(1.5, gantry) == "0"
    assert display_angle(1.5, ()) == "1.5"
    assert snap_angle(45.0, (90.0, 0.0)) == 90.0
    assert snap_angle(10.0, ()) == 10.0


def test_gtc_beam_name():
    from winstonlutz.analysis import AnalysisParams, gtc_beam_name

    params = AnalysisParams(
        nominal_gantry_angles=(270.0, 0.0, 90.0, 180.0),
        nominal_table_angles=(90.0, 45.0, 0.0, 315.0, 270.0),
        nominal_collimator_angles=(135.0, 90.0, 45.0, 0.0, 315.0, 270.0, 225.0),
    )
    assert gtc_beam_name(180.0, 0.0, 0.0, params) == "G180_T0_C0"
    assert gtc_beam_name(270.0, 90.0, 45.0, params) == "G270_T90_C45"
    assert gtc_beam_name(180.2, 1.0, 359.0, params) == "G180_T0_C0"


def test_default_machine_and_csv_numbers():
    from winstonlutz.app_settings import (
        default_machine,
        format_csv_numbers,
        parse_csv_numbers,
        parse_int_list,
    )

    machine = default_machine("Edge")
    assert machine["NAME"] == "Edge"
    assert machine["WATCH_FOLDER"] == ""
    assert "kV_field_search" not in machine
    assert machine["kV_field_search_method"] == "ImageCenter"
    assert machine["ALL_RI_IMAGE_REQUIRED"] is False
    assert machine["IGNORE_BEAMS"] == []
    assert machine["nominal_gantry_angles"] == [270, 0, 90, 180]
    assert parse_csv_numbers("270, 0, 90, 180") == [270, 0, 90, 180]
    assert parse_int_list("2, 7") == [2, 7]
    assert format_csv_numbers([270, 0, 90, 180]) == "270, 0, 90, 180"
    assert format_csv_numbers(50.0) == "50"


def test_is_simple_run_mode(tmp_path, monkeypatch):
    from winstonlutz.app_settings import (
        is_simple_run_mode,
        save_gui_settings,
        simple_machine_name,
    )

    cfg = tmp_path / SETTINGS_NAME
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(cfg))
    assert is_simple_run_mode() is True
    save_gui_settings({})
    assert is_simple_run_mode() is True
    save_gui_settings({"Institution": "Test", "MACHINES": []})
    assert is_simple_run_mode() is True
    save_gui_settings({"MACHINES": [{"DATA_FOLDER": "x"}]})
    assert is_simple_run_mode() is True
    save_gui_settings({"MACHINES": [{"NAME": "Edge"}]})
    assert is_simple_run_mode() is False
    save_gui_settings({"RunMode": "Simple", "MACHINES": [{"NAME": "Edge"}]})
    assert is_simple_run_mode() is True
    save_gui_settings({"RunMode": "Clinic", "MACHINES": [{"NAME": "Edge"}]})
    assert is_simple_run_mode() is False

    case = tmp_path / "Edge" / "26-09-24_06-13-24"
    case.mkdir(parents=True)
    assert simple_machine_name(case) == "Edge"
    nested = tmp_path / "Edge" / "Data" / "26-09-24_06-13-24"
    nested.mkdir(parents=True)
    assert simple_machine_name(nested) == "Edge"


def test_error_email_settings_and_addresses(monkeypatch):
    from winstonlutz.emailer import error_email_settings, recipient_addresses

    assert recipient_addresses("jinkoo.kim@stonybrookmedicine.edu") == [
        "jinkoo.kim@stonybrookmedicine.edu"
    ]
    assert recipient_addresses("jinkoo.kim", "stonybrookmedicine.edu") == [
        "jinkoo.kim@stonybrookmedicine.edu"
    ]
    assert recipient_addresses(
        ["jinkoo.kim@stonybrookmedicine.edu", "physics@stonybrookmedicine.edu"]
    ) == [
        "jinkoo.kim@stonybrookmedicine.edu",
        "physics@stonybrookmedicine.edu",
    ]
    assert recipient_addresses(["jinkoo.kim", "physics"], "stonybrookmedicine.edu") == [
        "jinkoo.kim@stonybrookmedicine.edu",
        "physics@stonybrookmedicine.edu",
    ]
    assert error_email_settings({}) is None
    assert error_email_settings({"error_email_to": ["a@b.c"]}) is None
    assert error_email_settings({"error_email_to": "a@b.c"}) is None
    cfg = error_email_settings(
        {
            "error_email_to": ["jinkoo.kim@stonybrookmedicine.edu"],
            "email_from": "radonc.physics",
            "email_domain": "stonybrookmedicine.edu",
            "email_host_address": "uhmc-imail.uhmc.sunysb.edu",
            "email_host_port": 25,
            "enable_ssl": False,
        }
    )
    assert cfg is not None
    assert cfg["to"] == ["jinkoo.kim@stonybrookmedicine.edu"]
    assert cfg["host"] == "uhmc-imail.uhmc.sunysb.edu"
    many = error_email_settings(
        {
            "error_email_to": [
                "jinkoo.kim@stonybrookmedicine.edu",
                "physics@stonybrookmedicine.edu",
            ],
            "email_from": "radonc.physics",
            "email_host_address": "uhmc-imail.uhmc.sunysb.edu",
        }
    )
    assert many is not None
    assert many["to"] == [
        "jinkoo.kim@stonybrookmedicine.edu",
        "physics@stonybrookmedicine.edu",
    ]
    nested = error_email_settings(
        {
            "Notifications": {
                "email": {
                    "error_email_to": ["a@b.c", "d@e.f"],
                    "email_from": "radonc.physics",
                    "email_host_address": "mail.example.edu",
                    "email_host_port": 25,
                }
            }
        }
    )
    assert nested is not None
    assert nested["to"] == ["a@b.c", "d@e.f"]
    assert nested["host"] == "mail.example.edu"
    from winstonlutz.emailer import event_email_settings, igrt_report_recipients

    assert event_email_settings({}) is None
    events = event_email_settings(
        {
            "Notifications": {
                "email": {
                    "event_email_to": ["jinkoo.kim@stonybrookmedicine.edu"],
                    "email_from": "radonc.physics",
                    "email_host_address": "mail.example.edu",
                }
            }
        }
    )
    assert events is not None
    assert events["to"] == ["jinkoo.kim@stonybrookmedicine.edu"]
    report_to = igrt_report_recipients(
        {"new_case_email_to": ["extra@hospital.edu"]},
        {
            "Notifications": {
                "email": {
                    "new_case_email_to": ["jinkoo.kim@stonybrookmedicine.edu"],
                    "email_from": "radonc.physics",
                    "email_host_address": "mail.example.edu",
                }
            }
        },
    )
    assert report_to == [
        "jinkoo.kim@stonybrookmedicine.edu",
        "extra@hospital.edu",
    ]
    same = igrt_report_recipients(
        {"new_case_email_to": ["jinkoo.kim@stonybrookmedicine.edu"]},
        {
            "Notifications": {
                "email": {
                    "new_case_email_to": ["jinkoo.kim@stonybrookmedicine.edu"],
                    "email_from": "radonc.physics",
                    "email_host_address": "mail.example.edu",
                }
            }
        },
    )
    assert same == ["jinkoo.kim@stonybrookmedicine.edu"]
    legacy = error_email_settings(
        {
            "error_email_to": "a@b.c, d@e.f",
            "email_from": "radonc.physics",
            "email_host_address": "mail.example.edu",
        }
    )
    assert legacy is not None
    assert legacy["to"] == ["a@b.c", "d@e.f"]
    from winstonlutz.app_settings import chat_webhook_urls
    from winstonlutz.emailer import post_chat_webhooks

    hooks = chat_webhook_urls(
        {"Notifications": {"google_chat": {"webhook_url": "https://chat.example/hook"}}}
    )
    assert hooks["google_chat"] == "https://chat.example/hook"
    assert hooks["slack"] == ""
    posted = []

    def fake_post(url, payload):
        posted.append((url, payload))

    monkeypatch.setattr("winstonlutz.emailer._post_webhook", fake_post)
    post_chat_webhooks(
        "boom",
        "traceback",
        context="test",
        data={
            "Notifications": {
                "slack": {"webhook_url": "https://hooks.slack.com/x"},
                "discord": {"webhook_url": "https://discord.com/api/webhooks/x"},
            }
        },
    )
    urls = {item[0] for item in posted}
    assert urls == {"https://hooks.slack.com/x", "https://discord.com/api/webhooks/x"}
    by_url = {item[0]: item[1] for item in posted}
    assert "text" in by_url["https://hooks.slack.com/x"]
    assert "content" in by_url["https://discord.com/api/webhooks/x"]

    from winstonlutz.emailer import send_test_chat, send_test_email

    try:
        send_test_email({})
        raise AssertionError("expected missing SMTP fields")
    except RuntimeError as exc:
        assert "email_from" in str(exc)
    posted.clear()
    try:
        send_test_chat("slack", {"Notifications": {"slack": {"webhook_url": ""}}})
        raise AssertionError("expected missing webhook")
    except RuntimeError as exc:
        assert "webhook_url" in str(exc)
    send_test_chat(
        "google_chat",
        {"Notifications": {"google_chat": {"webhook_url": "https://chat.example/hook"}}},
    )
    assert len(posted) == 1
    assert posted[0][0] == "https://chat.example/hook"
    assert "text" in posted[0][1]
    assert "notification test" in posted[0][1]["text"]
