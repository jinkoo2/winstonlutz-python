from pathlib import Path
import json

from winstonlutz.app_settings import (
    form_id_for_machine,
    format_form_ids,
    parse_form_ids_text,
)
from winstonlutz.docuforms_igrt import (
    MARKER_NAME,
    calculate_overall_result,
    field_prefix,
    performed_at_from_case,
    pick_unique_items,
    round_gantry,
    round_table,
    upload_case,
    values_from_items,
)
from winstonlutz.models import WinstonLutzItem
from winstonlutz.postprocess import run_post_processing


def test_form_id_for_machine():
    step = {
        "form_ids": [
            {"machine": "Edge", "form_id": "sb_edge_mlc_wl"},
            {"machine": "TrueBeamSH", "form_id": "pfcc_truebeamsh_mlc_wl"},
        ]
    }
    assert form_id_for_machine(step, {"NAME": "TrueBeamSH"}) == "pfcc_truebeamsh_mlc_wl"
    assert form_id_for_machine(step, {"NAME": "Edge_Cone"}) == ""
    assert (
        form_id_for_machine(
            step, {"NAME": "Edge", "docuforms2_form_id": "ignored"}
        )
        == "sb_edge_mlc_wl"
    )
    assert (
        form_id_for_machine({}, {"NAME": "Edge", "docuforms2_form_id": "legacy"})
        == "legacy"
    )
    assert parse_form_ids_text("Edge  sb_edge_mlc_wl\nTrueBeamSH=pfcc_truebeamsh_mlc_wl") == [
        {"machine": "Edge", "form_id": "sb_edge_mlc_wl"},
        {"machine": "TrueBeamSH", "form_id": "pfcc_truebeamsh_mlc_wl"},
    ]
    assert parse_form_ids_text("Edge = sb_edge_mlc_wl") == [
        {"machine": "Edge", "form_id": "sb_edge_mlc_wl"},
    ]
    assert format_form_ids(
        [{"machine": "Edge", "form_id": "sb_edge_mlc_wl"}]
    ) == "Edge = sb_edge_mlc_wl"


def test_round_angles():
    assert round_gantry(355) == 0
    assert round_gantry(360) == 0
    assert round_gantry(88) == 90
    assert round_table(315) == 310
    assert round_table(350) == 350
    assert round_table(360) == 0
    assert round_table(358) == 0


def test_performed_at_is_yy_mm_dd(tmp_path):
    case = tmp_path / "26-09-30_06-27-37"
    case.mkdir()
    stamp = performed_at_from_case(case)
    assert stamp is not None
    assert stamp.year == 2026
    assert stamp.month == 9
    assert stamp.day == 30
    (case / "report.html").write_text(
        '<span id="basic-addon1">Date/Time</span>\n'
        '<input type="text" class="form-control" value="26/09/30 06:27:37">',
        encoding="utf-8",
    )
    stamp = performed_at_from_case(case)
    assert stamp.isoformat() == "2026-09-30T06:27:37"


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
    assert "2026-09-24T06:13:24" in values["performed_at"]
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
        "form_ids": [{"machine": "Edge", "form_id": "sb_edge_mlc_wl"}],
    }
    assert upload_case(case, [item], {"NAME": "TrueBeamSH"}, step) == "skipped"
    assert upload_case(case, [item], {"NAME": "Edge"}, step) == "dry-run"
    (case / MARKER_NAME).write_text("{}", encoding="utf-8")
    step_live = {
        "backend_url": "https://example.invalid",
        "dry_run": False,
        "resubmit": False,
        "form_ids": [{"machine": "Edge", "form_id": "sb_edge_mlc_wl"}],
    }
    assert upload_case(case, [item], {"NAME": "Edge"}, step_live) == "skipped"


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
        {"NAME": "Edge", "WL_pass_tolerance": 1.0},
        {
            "backend_url": "https://roweb3.example:9001",
            "form_ids": [{"machine": "Edge", "form_id": "sb_edge_mlc_wl"}],
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


def test_upload_case_attaches_full_report_pdf(tmp_path, monkeypatch):
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    (case / "report.html").write_text("<html>full</html>", encoding="utf-8")
    (case / "report.short.html").write_text("<html>short</html>", encoding="utf-8")
    uploaded = []

    def fake_post(_url, payload, *, timeout, verify):
        uploaded.append(("submit", [a.get("originalName") for a in payload.get("attachments") or []]))
        return {"_id": "abc"}

    def fake_upload(_backend, path, name, **_k):
        uploaded.append(name)
        return {"url": f"/uploads/{name}", "originalName": name}

    def fake_html_to_pdf(html_path, dest):
        assert Path(html_path).name == "report.html"
        assert "short" not in Path(html_path).name
        Path(dest).write_bytes(b"%PDF-1.4 dummy")
        return True

    monkeypatch.setattr("winstonlutz.docuforms_igrt.post_json", fake_post)
    monkeypatch.setattr("winstonlutz.docuforms_igrt.upload_file", fake_upload)
    monkeypatch.setattr("winstonlutz.docuforms_igrt.html_to_pdf", fake_html_to_pdf)
    item = WinstonLutzItem(
        gantry=0, table=0, MV=True, DCM="RI.1.dcm",
        bb_offset_from_field_center=[0.1, 0.1],
    )
    status = upload_case(
        case,
        [item],
        {"docuforms2_form_id": "sb_edge_mlc_wl"},
        {
            "backend_url": "https://roweb3.example:9001",
            "attach_dcm_zip": False,
            "attach_pdf": True,
            "verify_ssl": False,
        },
    )
    assert status == "ok"
    assert "report.pdf" in uploaded
    assert (case / "report.pdf").is_file()
    marker = json.loads((case / MARKER_NAME).read_text(encoding="utf-8"))
    assert "report.pdf" in marker.get("attachments", [])


def test_calculate_overall_result():
    assert calculate_overall_result({"a": {"result": "PASS"}, "b": {"result": "FAIL"}}) == "FAIL"
    assert calculate_overall_result({"a": {"result": "PASS"}}) == "PASS"


def test_upload_case_emails_success_event_to(tmp_path, monkeypatch):
    sent = []

    def fake_send(to, message, details="", **_kwargs):
        sent.append((list(to) if not isinstance(to, str) else to, message, details))

    monkeypatch.setattr("winstonlutz.emailer.send_list_email", fake_send)
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    item = WinstonLutzItem(gantry=0, table=0, MV=True, DCM="RI.1.dcm")
    step = {
        "backend_url": "https://example.invalid",
        "dry_run": True,
        "attach_dcm_zip": False,
        "email_success_event_to": ["jinkoo.kim@stonybrookmedicine.edu"],
    }
    assert upload_case(
        case, [item], {"NAME": "Edge", "docuforms2_form_id": "sb_edge_mlc_wl"}, step
    ) == "dry-run"
    assert sent
    to, message, details = sent[0]
    assert to == ["jinkoo.kim@stonybrookmedicine.edu"]
    assert "dry-run" in message
    assert "Edge/26-09-24_06-13-24" in message
    assert "status=dry-run" in details


def test_run_post_processing_emails_failure(tmp_path, monkeypatch):
    events = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("backend down")

    monkeypatch.setattr("winstonlutz.docuforms_igrt.upload_case", boom)
    monkeypatch.setattr("winstonlutz.postprocess.send_error_email", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "winstonlutz.docuforms_igrt.notify_docuforms_event",
        lambda step, status, case_dir, machine, extra=None: events.append(
            (status, extra or {})
        ),
    )
    run_post_processing(
        tmp_path,
        {"NAME": "Edge", "docuforms2_form_id": "sb_edge_mlc_wl"},
        [WinstonLutzItem()],
        data={
            "PostProcessing": [
                {
                    "type": "docuforms2_igrt",
                    "enabled": True,
                    "backend_url": "https://example.invalid",
                    "email_failure_event_to": ["jinkoo.kim@stonybrookmedicine.edu"],
                }
            ]
        },
    )
    assert events == [("failed", {"error": "backend down"})]


def test_skipped_does_not_email(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "winstonlutz.emailer.send_list_email",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    case = tmp_path / "26-09-24_06-13-24"
    case.mkdir()
    item = WinstonLutzItem(gantry=0, table=0, MV=True, DCM="RI.1.dcm")
    assert (
        upload_case(
            case,
            [item],
            {"NAME": "Edge", "docuforms2_form_id": ""},
            {
                "backend_url": "https://example.invalid",
                "email_success_event_to": ["ok@hospital.edu"],
                "email_failure_event_to": ["fail@hospital.edu"],
            },
        )
        == "skipped"
    )
    assert sent == []


def test_notify_failed_uses_failure_list(tmp_path, monkeypatch):
    from winstonlutz.docuforms_igrt import notify_docuforms_event

    sent = []
    monkeypatch.setattr(
        "winstonlutz.emailer.send_list_email",
        lambda to, message, details="", **kwargs: sent.append(to),
    )
    notify_docuforms_event(
        {
            "email_success_event_to": ["ok@hospital.edu"],
            "email_failure_event_to": ["fail@hospital.edu"],
        },
        "failed",
        tmp_path,
        {"NAME": "Edge"},
        {"error": "backend down"},
    )
    assert sent == [["fail@hospital.edu"]]
