import json
import os
import shutil
from pathlib import Path

from winstonlutz.rtplan import (
    beam_expects_image,
    find_machine_rtplan,
    list_plan_beams,
    machine_all_ri_required,
    machine_ignore_beams,
    missing_required_beams,
    plan_cache_path,
)

PLAN = Path(__file__).resolve().parents[1] / "sample_data" / "Edge" / "Plan" / "RP.EdgeDryRun.WL.dcm"


def test_edge_rtplan_beams():
    if not PLAN.is_file():
        return
    beams = list_plan_beams(PLAN)
    assert [b.number for b in beams] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    by_no = {b.number: b for b in beams}
    assert by_no[1].name == "G180E"
    assert by_no[1].kind == "MV"
    assert by_no[1].gantry == 180
    assert by_no[1].table == 0
    assert by_no[1].collimator == 0
    assert by_no[8].kind == "kV"
    assert by_no[8].gantry == 90
    assert by_no[9].kind == "kV"
    assert by_no[9].gantry == 0
    assert by_no[2].kind == "kV"
    assert by_no[2].name == "CBCT"
    assert not beam_expects_image(by_no[2])
    assert not beam_expects_image(by_no[2], {2})
    assert not beam_expects_image(by_no[1], {1})
    assert beam_expects_image(by_no[1])
    assert by_no[6].name == "G180E_T50"
    assert by_no[6].gantry == 180
    assert by_no[6].table == 50
    assert by_no[7].name == "G180E_T310"
    assert by_no[7].table == 310
    kinds = {b.kind for b in beams}
    assert kinds == {"MV", "kV"}


def test_find_machine_rtplan():
    if not PLAN.is_file():
        return
    machine = {
        "DATA_FOLDER": str(PLAN.parent.parent / "Data"),
    }
    found = find_machine_rtplan(machine)
    assert found is not None
    assert found.resolve() == PLAN.resolve()
    explicit = find_machine_rtplan({"DICOM_PLAN_FILE": str(PLAN)})
    assert explicit is not None
    assert explicit.resolve() == PLAN.resolve()


def test_match_ri_to_plan_beams():
    from winstonlutz.pipeline import list_ri_files
    from winstonlutz.rtplan import match_ri_files_to_beams, referenced_beam_number

    if not PLAN.is_file():
        return
    case = PLAN.parent.parent / "Data" / "26-09-25_06-15-37"
    if not case.is_dir():
        return
    beams = list_plan_beams(PLAN)
    files = list_ri_files(case)
    matched, unmatched = match_ri_files_to_beams(files, beams)
    assert unmatched == []
    assert 2 not in matched
    assert missing_required_beams(beams, matched) == []
    assert set(matched) == {1, 3, 4, 5, 6, 7, 8, 9}
    assert referenced_beam_number(matched[9]) == 9
    assert machine_all_ri_required({}) is False
    assert machine_all_ri_required({"ALL_RI_IMAGE_REQUIRED": False}) is False
    assert machine_all_ri_required({"ALL_RI_IMAGE_REQUIRED": True}) is True


def test_all_ri_required_ignores_missing_cbct():
    from winstonlutz.pipeline import case_has_missing_required_ri

    if not PLAN.is_file():
        return
    case = PLAN.parent.parent / "Data" / "26-09-25_06-15-37"
    if not case.is_dir():
        return
    machine = {"DICOM_PLAN_FILE": str(PLAN), "ALL_RI_IMAGE_REQUIRED": True}
    assert case_has_missing_required_ri(case, machine) is False
    machine_off = {"DICOM_PLAN_FILE": str(PLAN), "ALL_RI_IMAGE_REQUIRED": False}
    assert case_has_missing_required_ri(case, machine_off) is False
    machine_ignore = {
        "DICOM_PLAN_FILE": str(PLAN),
        "ALL_RI_IMAGE_REQUIRED": True,
        "IGNORE_BEAMS": [2],
    }
    assert case_has_missing_required_ri(case, machine_ignore) is False


def test_machine_ignore_beams():
    assert machine_ignore_beams(None) == set()
    assert machine_ignore_beams({}) == set()
    assert machine_ignore_beams({"IGNORE_BEAMS": [2]}) == {2}
    assert machine_ignore_beams({"IGNORE_BEAMS": "2, 7"}) == {2, 7}
    beams = list_plan_beams(PLAN) if PLAN.is_file() else []
    if not beams:
        return
    assert missing_required_beams(beams, {}, {2}) == [
        b for b in beams if b.number != 2 and beam_expects_image(b)
    ]


def test_plan_beam_json_cache():
    if not PLAN.is_file():
        return
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as folder:
        tmp_path = Path(folder)
        rp = tmp_path / PLAN.name
        shutil.copy2(PLAN, rp)
        cache = plan_cache_path(rp)
        assert cache == tmp_path / (PLAN.name + ".json")
        assert not cache.is_file()

        beams = list_plan_beams(rp)
        assert cache.is_file()
        assert [b.number for b in beams] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert list_plan_beams(rp) == beams

        payload = json.loads(cache.read_text(encoding="utf-8"))
        payload["beams"][0]["name"] = "CACHED"
        cache.write_text(json.dumps(payload), encoding="utf-8")
        rp_mtime = rp.stat().st_mtime
        os.utime(cache, (rp_mtime + 10, rp_mtime + 10))
        cached = list_plan_beams(rp)
        assert cached[0].name == "CACHED"

        os.utime(rp, (rp_mtime + 20, rp_mtime + 20))
        refreshed = list_plan_beams(rp)
        assert refreshed[0].name == "G180E"
        assert json.loads(cache.read_text(encoding="utf-8"))["beams"][0]["name"] == "G180E"
