from pathlib import Path

from winstonlutz.docuforms_igrt import (
    MARKER_NAME,
    calculate_overall_result,
    field_prefix,
    pick_unique_items,
    round_gantry,
    round_table,
    upload_case,
    values_from_items,
)
from winstonlutz.models import WinstonLutzItem
from winstonlutz.postprocess import run_post_processing


def test_round_angles():
    assert round_gantry(355) == 0
    assert round_gantry(360) == 0
    assert round_gantry(88) == 90
    assert round_table(315) == 310
    assert round_table(350) == 350
    assert round_table(360) == 0
    assert round_table(358) == 0


def test_values_from_items_and_duplicates(tmp_path):
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    png = case / "RI.1.dcm_out" / "result.png"
    png.parent.mkdir()
    png.write_bytes(b"\x89PNG")
    pass_item = WinstonLutzItem(
        gantry=180, table=0, MV=True, DCM=str(case / "RI.1.dcm"),
        bb_offset_from_field_center=[0.1, 0.2],
        bb_center=[0.1, 0.3],
        field_center=[0.0, 0.0],
        user="cszarka",
    )
    fail_dup = WinstonLutzItem(
        gantry=180, table=0, MV=True, DCM=str(case / "RI.1.dcm"),
        bb_offset_from_field_center=[2.0, 0.0],
        bb_center=[2.0, 0.0],
        field_center=[0.0, 0.0],
    )
    kv = WinstonLutzItem(
        gantry=0, table=0, MV=False, DCM=str(case / "RI.1.dcm"),
        bb_offset_from_field_center=[0.1, 0.1],
        bb_center=[-0.1, -0.1],
        field_center=[0.0, 0.0],
    )
    picked = pick_unique_items([fail_dup, pass_item, kv], tol=1.0)
    assert len(picked) == 2
    mv = [i for i in picked if i.MV][0]
    assert mv.calc_norm_of_bb_offset_from_field_center() < 1.0
    values, overall, panels = values_from_items(
        case, [fail_dup, pass_item, kv], tol=1.0, performed_by="cszarka"
    )
    assert values["performed_by"] == "cszarka"
    assert "2024-09-26T06:13:24" in values["performed_at"]
    assert field_prefix(pass_item) == "g180_t0_mv"
    assert "g180_t0_mv_bb_fc_d" in values
    assert "g0_t0_kv_bb_ic_d" in values
    assert "g0_t0_kv_bb_fc_d" not in values
    assert values["img_g180_t0_mv"].startswith("data:image/png;base64,")
    assert overall == "PASS"
    assert panels["g180_t0_mv"] == "PASS"


def test_upload_case_dry_run_and_skip(tmp_path, monkeypatch):
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    item = WinstonLutzItem(gantry=0, table=0, MV=True, DCM="RI.1.dcm")
    step = {
        "backend_url": "https://example.invalid",
        "dry_run": True,
        "attach_dcm_zip": False,
    }
    assert upload_case(case, [item], {"docuforms2_form_id": ""}, step) == "skipped"
    assert upload_case(case, [item], {"docuforms2_form_id": "sb_edge_mlc_wl"}, step) == "dry-run"
    (case / MARKER_NAME).write_text("{}", encoding="utf-8")
    step_live = {"backend_url": "https://example.invalid", "dry_run": False, "resubmit": False}
    assert upload_case(case, [item], {"docuforms2_form_id": "sb_edge_mlc_wl"}, step_live) == "skipped"


def test_run_post_processing_disabled(tmp_path, monkeypatch):
    called = []

    def boom(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("should not run")

    monkeypatch.setattr("winstonlutz.docuforms_igrt.upload_case", boom)
    run_post_processing(
        tmp_path,
        {"docuforms2_form_id": "sb_edge_mlc_wl"},
        [WinstonLutzItem()],
        data={
            "PostProcessing": [
                {
                    "type": "docuforms2_igrt",
                    "enabled": False,
                    "backend_url": "https://example.invalid",
                }
            ]
        },
    )
    assert called == []


def test_upload_case_posts(tmp_path, monkeypatch):
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    (case / "RI.1.dcm").write_bytes(b"DICM")
    posted = []

    def fake_post(url, payload, *, timeout, verify):
        posted.append((url, payload))
        return {"ok": True}

    def fake_upload(*_a, **_k):
        return {"url": "/uploads/x.zip", "originalName": "input_dcm.zip"}

    monkeypatch.setattr("winstonlutz.docuforms_igrt.post_json", fake_post)
    monkeypatch.setattr("winstonlutz.docuforms_igrt.upload_file", fake_upload)
    item = WinstonLutzItem(
        gantry=270, table=0, MV=True, DCM=str(case / "RI.1.dcm"),
        bb_offset_from_field_center=[0.2, 0.1],
        user="phys",
    )
    status = upload_case(
        case,
        [item],
        {"docuforms2_form_id": "sb_edge_mlc_wl", "WL_pass_tolerance": 1.0},
        {
            "backend_url": "https://roweb3.example:9001",
            "attach_dcm_zip": True,
            "attach_pdf": False,
            "verify_ssl": False,
        },
    )
    assert status == "ok"
    assert (case / MARKER_NAME).is_file()
    assert posted
    url, payload = posted[0]
    assert url.endswith("/api/forms/sb_edge_mlc_wl/submit")
    assert payload["values"]["g270_t0_mv_bb_fc_d"] == "0.2"
    assert payload["attachments"][0]["originalName"] == "input_dcm.zip"


def test_calculate_overall_result():
    assert calculate_overall_result({"a": {"result": "PASS"}, "b": {"result": "FAIL"}}) == "FAIL"
    assert calculate_overall_result({"a": {"result": "PASS"}}) == "PASS"
