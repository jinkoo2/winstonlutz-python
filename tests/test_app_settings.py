from winstonlutz.app_settings import SETTINGS_NAME, app_dir, user_config_path
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
    assert "kV_field_search" not in machine
    assert machine["kV_field_search_method"] == "ImageCenter"
    assert machine["ALL_RI_IMAGE_REQUIRED"] is False
    assert machine["IGNORE_BEAMS"] == []
    assert machine["nominal_gantry_angles"] == [270, 0, 90, 180]
    assert parse_csv_numbers("270, 0, 90, 180") == [270, 0, 90, 180]
    assert parse_int_list("2, 7") == [2, 7]
    assert format_csv_numbers([270, 0, 90, 180]) == "270, 0, 90, 180"
    assert format_csv_numbers(50.0) == "50"
