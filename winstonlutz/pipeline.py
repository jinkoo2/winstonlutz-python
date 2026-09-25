"""Case-level pipeline matching WinstonLutzLib.WinstonLutz.Run."""

from __future__ import annotations

import logging
from pathlib import Path

from .analysis import (
    AnalysisSkip,
    analyze_image,
    bb_method_from_log,
    classify_rt_image,
    normalize_bb_method,
    parse_result_txt,
)
from .config import Param, find_machine_config
from .emailer import send_from_config
from .models import WinstonLutzItem, calc_norm, save_items_json

logger = logging.getLogger(__name__)

CASE_NAME_LEN = 17
MV_CRITERIA = "0028|0011=1190&0028|0010=1190"
KV_CRITERIA = "0028|0011=1024&0028|0010=768"


def validate_case_dir_name(case_dir: Path) -> None:
    name = case_dir.name
    if len(name) != CASE_NAME_LEN:
        raise ValueError("case directory string length must be 17.")
    if name.count("_") != 1:
        raise ValueError("case directory string must has one '_'.")
    if name.count("-") != 4:
        raise ValueError("case directory string must has four '-'.")


def _bb_method(name: str) -> str:
    name = (name or "ConnectedComponent").strip()
    if name.startswith("bb_search_"):
        return name
    return f"bb_search_{name}"


def _item_from_result(dcm: Path, parsed: dict, mv: bool) -> WinstonLutzItem:
    return WinstonLutzItem(
        gantry=float(parsed.get("gantry") or 0),
        table=float(parsed.get("table") or 0),
        collimator=float(parsed.get("collimator") or 0),
        field_center=list(parsed.get("field center") or [0.0, 0.0]),
        bb_center=list(parsed.get("bb_cetner") or [0.0, 0.0]),
        bb_offset_from_field_center=list(parsed.get("bb offset") or [0.0, 0.0]),
        DCM=str(dcm),
        user=str(parsed.get("operator") or ""),
        MV=mv,
        sid_mm=float(parsed.get("sid_mm") or 1500.0),
        bb_method=normalize_bb_method(str(parsed.get("bb_search") or "")),
    )


def analyze_ri(
    dcm: Path,
    mv_method: str,
    kv_method: str,
    preprocess: bool = False,
) -> Path | None:
    out_dir = Path(str(dcm) + "_out")
    kind = classify_rt_image(dcm)
    if kind == "MV":
        field_search = "field_search_yes"
        bb_search = _bb_method(mv_method)
    elif kind == "kV":
        field_search = "field_search_no"
        bb_search = _bb_method(kv_method)
    else:
        logger.info("could not classify %s as MV or kV; skipping", dcm.name)
        return None
    try:
        analyze_image(
            dcm,
            out_dir,
            field_search=field_search,
            bb_search=bb_search,
            match_criteria="",
            preprocess=preprocess,
        )
        return out_dir
    except AnalysisSkip:
        logger.info("analysis skipped for %s", dcm.name)
        return None


def _case_stamp(folder: Path) -> tuple[str, str]:
    parts = folder.name.split("_", 1)
    date = parts[0].replace("-", "/")
    time = parts[1].replace("-", ":") if len(parts) > 1 else ""
    return date, time


def find_report_template(folder: str | Path) -> Path | None:
    folder = Path(folder)
    for candidate in (
        folder.parent.parent / "ReportTmplt",
        folder.parent / "ReportTmplt",
        folder / "ReportTmplt",
    ):
        if (candidate / "full" / "report1.tmpl.html").is_file():
            return candidate
    return None


def find_html_report(folder: str | Path) -> Path | None:
    folder = Path(folder)
    for name in ("report.html", "report.short.html"):
        path = folder / name
        if path.is_file():
            return path
    return None


def write_html_reports(
    folder: str | Path,
    items: list[WinstonLutzItem],
    tol: float = 1.0,
) -> Path | None:
    """Write report.html using the machine template, or a simple fallback."""
    folder = Path(folder)
    if not items:
        return None
    tmpl_root = find_report_template(folder)
    if tmpl_root is not None:
        _render_reports(folder, items, tmpl_root, tol)
    else:
        _write_simple_report(folder, items, tol)
    return find_html_report(folder)


def _write_simple_report(folder: Path, items: list[WinstonLutzItem], tol: float) -> None:
    rows = []
    pass_all = True
    for item in items:
        d = item.calc_norm_of_bb_offset_from_field_center()
        ok = d <= tol
        if not ok:
            pass_all = False
        off = item.bb_offset_from_field_center
        img = Path(item.DCM).name + "_out/result.png"
        cls = "pass" if ok else "fail"
        rows.append(
            "<div class='item'>"
            f"<h3>G={item.gantry:.0f}, T={item.table:.0f}, C={item.collimator:.0f} "
            f"[{'MV' if item.MV else 'kV'}] "
            f"<span class='{cls}'>{'Pass' if ok else 'Fail'}</span></h3>"
            f"<p>BB-FC = {off[0]:.2f}, {off[1]:.2f} mm &nbsp; d={d:.2f} mm</p>"
            f"<p><img src='{img}' alt='result'></p>"
            "</div>"
        )
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Winston-Lutz {folder.name}</title>"
        "<style>body{font-family:sans-serif;margin:24px}"
        ".pass{color:#009600;font-weight:bold}.fail{color:#c80000;font-weight:bold}"
        "img{max-width:420px;border:1px solid #ccc}</style></head><body>"
        f"<h1>Winston-Lutz report — {folder.name}</h1>"
        f"<p>images={len(items)} &nbsp; tol={tol:.1f} mm &nbsp; "
        f"<span class='{'pass' if pass_all else 'fail'}'>"
        f"{'Pass' if pass_all else 'Fail'}</span></p>"
        + "\n".join(rows)
        + "</body></html>"
    )
    (folder / "report.html").write_text(html, encoding="utf-8")


def _render_reports(case_dir: Path, items: list[WinstonLutzItem], tmpl_root: Path, tol: float) -> None:
    date, time = _case_stamp(case_dir)
    pass_html = '<span class="label label-success">Pass</span>'
    fail_html = '<span class="label label-danger">Fail</span>'

    def one_report(kind: str, report_name: str) -> None:
        item_tmpl = (tmpl_root / kind / "item.html").read_text(encoding="utf-8", errors="replace")
        page_tmpl = (tmpl_root / kind / "report1.tmpl.html").read_text(encoding="utf-8", errors="replace")
        blocks = []
        pass_all = True
        for item in items:
            block = item_tmpl
            mv_kv = "MV" if item.MV else "kV"
            title = f"G={item.gantry:.0f}, T={item.table:.0f}, C={item.collimator:.0f} [{mv_kv}]"
            block = block.replace("{{{title}}}", title)
            out_dir_name = Path(item.DCM).name + "_out"
            block = block.replace("{{{img}}}", f".\\{out_dir_name}\\result.png")
            off = item.bb_offset_from_field_center
            block = block.replace(
                "{{{bb_offset_from_field_center}}}",
                f"{off[0]:.1f},{off[1]:.1f}, d={calc_norm(off):.1f}",
            )
            block = block.replace(
                "{{{bb_offset_from_image_center}}}",
                f"{item.bb_center[0]:.1f},{item.bb_center[1]:.1f}, d={calc_norm(item.bb_center):.1f}",
            )
            block = block.replace(
                "{{{field_offset_from_image_center}}}",
                f"{item.field_center[0]:.1f},{item.field_center[1]:.1f}, d={calc_norm(item.field_center):.1f}",
            )
            if item.MV:
                block = block.replace("{{{heading_type}}}", "panel-primary")
                block = block.replace("{{{style}}}", "")
            else:
                block = block.replace("{{{heading_type}}}", "panel-danger")
                block = block.replace("{{{style}}}", "display:none;")
            if item.calc_norm_of_bb_offset_from_field_center() <= tol:
                block = block.replace("{{{pass_fail}}}", pass_html)
            else:
                block = block.replace("{{{pass_fail}}}", fail_html)
                pass_all = False
            blocks.append(block)

        html = page_tmpl
        html = html.replace("{{{date}}}", date)
        html = html.replace("{{{time}}}", time)
        html = html.replace("{{{user}}}", items[0].user if items else "")
        html = html.replace("{{{tol}}}", str(tol))
        html = html.replace("{{{result}}}", "Pass" if pass_all else "Fail")
        html = html.replace("{{{full_report}}}", str(case_dir / "report.html"))
        html = html.replace("{{{body}}}", "\n".join(blocks))
        (case_dir / report_name).write_text(html, encoding="utf-8")

    if (tmpl_root / "full" / "item.html").is_file():
        one_report("full", "report.html")
    if (tmpl_root / "short" / "item.html").is_file():
        one_report("short", "report.short.html")


def run_case(
    case_dir: str | Path,
    data_root: str | Path | None = None,
    machine: str | None = None,
    send_email: bool = False,
    preprocess: bool = False,
) -> list[WinstonLutzItem]:
    case_dir = Path(case_dir)
    validate_case_dir_name(case_dir)

    config_path = find_machine_config(case_dir)
    param = Param(config_path) if config_path else None
    if machine is None:
        if param:
            machine = param.get_value("machine")
        if not machine:
            machine = case_dir.parent.name
            if machine.lower() == "data":
                machine = case_dir.parent.parent.name

    if data_root is None:
        if case_dir.parent.name.lower() == "data":
            data_root = case_dir.parent.parent.parent
        else:
            data_root = case_dir.parent.parent
    data_root = Path(data_root)
    machine_root = data_root / machine

    mv_method = param.get_value("MV_bb_search_method") if param else ""
    kv_method = param.get_value("kV_bb_search_method") if param else ""
    if not mv_method:
        mv_method = "LoG" if machine.lower() == "truebeam" else "ConnectedComponent"
    if not kv_method:
        kv_method = "ConnectedComponent"

    items: list[WinstonLutzItem] = []
    for dcm in sorted(case_dir.glob("RI.*.dcm")):
        out_dir = analyze_ri(dcm, mv_method, kv_method, preprocess=preprocess)
        result_file = (out_dir / "result.txt") if out_dir else None
        if result_file is None or not result_file.is_file():
            logger.info("result file not found... so skipping...: %s", dcm)
            continue
        parsed = parse_result_txt(result_file)
        mv = (out_dir / "img.field.mask.mhd").is_file() if out_dir else True
        items.append(_item_from_result(dcm, parsed, mv))

    if not items:
        logger.warning("no Winston-Lutz results in %s", case_dir)
        return items

    json_dir = machine_root / "Data" / "Json"
    if not json_dir.is_dir():
        json_dir = machine_root / "Data" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    save_items_json(items, json_dir / f"{case_dir.name}.json")

    if param:
        csv_file = param.get_value("record_csv_file")
        if csv_file:
            max_item = max(items, key=lambda i: i.calc_norm_of_bb_offset_from_field_center())
            date = case_dir.name.split("_")[0].replace("-", "/")
            time = case_dir.name.split("_")[1].replace("-", ":")
            line = "{},{},{},{},{}\n".format(
                date,
                time,
                items[0].user,
                max_item.calc_norm_of_bb_offset_from_field_center(),
                max_item.get_machine_param_string(";"),
            )
            csv_path = Path(csv_file)
            try:
                with csv_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                logger.warning("could not append csv %s: %s", csv_path, exc)

    tmpl_root = machine_root / "ReportTmplt"
    tol = param.get_float("WL_pass_tolerance", 1.0) if param else 1.0
    if tmpl_root.is_dir():
        _render_reports(case_dir, items, tmpl_root, tol or 1.0)

    if send_email and param is not None:
        short = case_dir / "report.short.html"
        if short.is_file():
            send_from_config(param, f"IGRT ({machine})", short.read_text(encoding="utf-8"))

    return items


def list_ri_files(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    files = sorted(folder.glob("RI.*.dcm"))
    if not files:
        files = sorted(
            p for p in folder.glob("*.dcm") if p.name.upper().startswith("RI.")
        )
    return files


def load_existing_results(folder: str | Path) -> list[WinstonLutzItem]:
    items: list[WinstonLutzItem] = []
    for dcm in list_ri_files(folder):
        result_file = Path(str(dcm) + "_out") / "result.txt"
        if not result_file.is_file():
            continue
        parsed = parse_result_txt(result_file)
        mv = (result_file.parent / "img.field.mask.mhd").is_file()
        items.append(_item_from_result(dcm, parsed, mv))
    return items


def infer_folder_bb_methods(
    folder: str | Path,
    items: list[WinstonLutzItem] | None = None,
) -> tuple[str, str]:
    """Return (mv_method, kv_method) from result.txt, then config.txt, then log.txt."""
    folder = Path(folder)
    if items is None:
        items = load_existing_results(folder)

    mv = ""
    kv = ""
    if items:
        for item in items:
            method = normalize_bb_method(item.bb_method)
            if not method:
                continue
            if item.MV and not mv:
                mv = method
            if not item.MV and not kv:
                kv = method

    if not mv or not kv:
        config_path = find_machine_config(folder)
        if config_path:
            param = Param(config_path)
            if not mv:
                mv = normalize_bb_method(param.get_value("MV_bb_search_method"))
            if not kv:
                kv = normalize_bb_method(param.get_value("kV_bb_search_method"))

    # New cases have no result.txt: stop at config.txt (do not parse leftover logs).
    if items and (not mv or not kv):
        for dcm in list_ri_files(folder):
            log_file = Path(str(dcm) + "_out") / "log.txt"
            method = normalize_bb_method(bb_method_from_log(log_file))
            if not method:
                continue
            is_mv = (log_file.parent / "img.field.mask.mhd").is_file()
            if is_mv and not mv:
                mv = method
            if not is_mv and not kv:
                kv = method
            if mv and kv:
                break
    return mv, kv


def analyze_folder(
    folder: str | Path,
    mv_method: str = "ConnectedComponent",
    kv_method: str = "ConnectedComponent",
    preprocess: bool = False,
    write_reports: bool = False,
) -> list[WinstonLutzItem]:
    """Analyze every RI DICOM in a folder. Does not require the YY-MM-DD case name."""
    folder = Path(folder)
    items: list[WinstonLutzItem] = []
    for dcm in list_ri_files(folder):
        out_dir = analyze_ri(dcm, mv_method, kv_method, preprocess=preprocess)
        result_file = (out_dir / "result.txt") if out_dir else None
        if result_file is None or not result_file.is_file():
            logger.info("result file not found... so skipping...: %s", dcm)
            continue
        parsed = parse_result_txt(result_file)
        mv = (out_dir / "img.field.mask.mhd").is_file() if out_dir else True
        items.append(_item_from_result(dcm, parsed, mv))
    if write_reports and items:
        write_html_reports(folder, items)
    return items
