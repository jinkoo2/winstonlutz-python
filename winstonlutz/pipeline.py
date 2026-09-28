"""Case-level pipeline matching WinstonLutzLib.WinstonLutz.Run."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .analysis import (
    AnalysisSkip,
    AnalysisParams,
    analyze_image,
    analysis_params_from_machine,
    bb_method_from_log,
    classify_rt_image,
    display_angle,
    normalize_bb_method,
    parse_result_txt,
    use_field_mask,
)
from .app_settings import DEFAULT_CASE_FOLDER_REGEX, find_machine_for_folder, report_template_root
from .config import Param, find_machine_config
from .emailer import send_from_config
from .models import WinstonLutzItem, calc_norm, save_items_json
from .rtplan import (
    find_machine_rtplan,
    list_plan_beams,
    machine_all_ri_required,
    machine_ignore_beams,
    match_ri_files_to_beams,
    missing_required_beams,
)

logger = logging.getLogger(__name__)

MV_CRITERIA = "0028|0011=1190&0028|0010=1190"
KV_CRITERIA = "0028|0011=1024&0028|0010=768"


def validate_case_dir_name(case_dir: Path, pattern: str | None = None) -> None:
    name = case_dir.name
    regex = (pattern or "").strip() or DEFAULT_CASE_FOLDER_REGEX
    if re.fullmatch(regex, name):
        return
    raise ValueError(f"case directory '{name}' does not match {regex}")


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
    params: AnalysisParams | None = None,
) -> Path | None:
    out_dir = Path(str(dcm) + "_out")
    params = params or analysis_params_from_machine(None)
    kind = classify_rt_image(dcm, params)
    if kind == "MV":
        field_search = "field_search_yes" if use_field_mask("MV", params) else "field_search_no"
        bb_search = _bb_method(mv_method)
    elif kind == "kV":
        field_search = "field_search_yes" if use_field_mask("kV", params) else "field_search_no"
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
            params=params,
            is_mv=kind == "MV",
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
    machine = find_machine_for_folder(folder)
    if machine:
        tmpl_root = report_template_root(machine)
        if tmpl_root is not None and (tmpl_root / "full" / "report1.tmpl.html").is_file():
            return tmpl_root
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


def _gtc_label(item: WinstonLutzItem, params: AnalysisParams, *, decimals: int = 0) -> str:
    g = display_angle(item.gantry, params.nominal_gantry_angles, decimals=decimals)
    t = display_angle(item.table, params.nominal_table_angles, decimals=decimals)
    c = display_angle(item.collimator, params.nominal_collimator_angles, decimals=decimals)
    return f"G={g}, T={t}, C={c}"


def _gtc_csv(item: WinstonLutzItem, params: AnalysisParams, delim: str = ";") -> str:
    g = display_angle(item.gantry, params.nominal_gantry_angles, decimals=0)
    t = display_angle(item.table, params.nominal_table_angles, decimals=0)
    c = display_angle(item.collimator, params.nominal_collimator_angles, decimals=0)
    return f"G={g}{delim}T={t}{delim}C={c}"


def write_html_reports(
    folder: str | Path,
    items: list[WinstonLutzItem],
    tol: float = 1.0,
    params: AnalysisParams | None = None,
    case_passed: bool | None = None,
) -> Path | None:
    """Write report.html using the machine template, or a simple fallback."""
    folder = Path(folder)
    if not items:
        return None
    params = params or analysis_params_from_machine(find_machine_for_folder(folder))
    tmpl_root = find_report_template(folder)
    if tmpl_root is not None:
        _render_reports(folder, items, tmpl_root, tol, params, case_passed=case_passed)
    else:
        _write_simple_report(folder, items, tol, params)
    return find_html_report(folder)


def _write_simple_report(
    folder: Path,
    items: list[WinstonLutzItem],
    tol: float,
    params: AnalysisParams,
) -> None:
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
            f"<h3>{_gtc_label(item, params)} "
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


def _render_reports(
    case_dir: Path,
    items: list[WinstonLutzItem],
    tmpl_root: Path,
    tol: float,
    params: AnalysisParams,
    case_passed: bool | None = None,
) -> None:
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
            title = f"{_gtc_label(item, params)} [{mv_kv}]"
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
        overall = pass_all if case_passed is None else bool(case_passed)
        html = html.replace("{{{result}}}", "Pass" if overall else "Fail")
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
    machine_cfg = find_machine_for_folder(case_dir)
    validate_case_dir_name(
        case_dir,
        str(machine_cfg.get("CASE_FOLDER_NAME_REGEX") or "") if machine_cfg else None,
    )

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

    mv_method = str((machine_cfg or {}).get("MV_bb_search_method") or "")
    kv_method = str((machine_cfg or {}).get("kV_bb_search_method") or "")
    if not mv_method:
        mv_method = param.get_value("MV_bb_search_method") if param else ""
    if not kv_method:
        kv_method = param.get_value("kV_bb_search_method") if param else ""
    if not mv_method:
        mv_method = "LoG" if machine.lower() == "truebeam" else "ConnectedComponent"
    if not kv_method:
        kv_method = "ConnectedComponent"

    params = analysis_params_from_machine(machine_cfg)
    items: list[WinstonLutzItem] = []
    for dcm in sorted(case_dir.glob("RI.*.dcm")):
        out_dir = analyze_ri(dcm, mv_method, kv_method, preprocess=preprocess, params=params)
        result_file = (out_dir / "result.txt") if out_dir else None
        if result_file is None or not result_file.is_file():
            logger.info("result file not found... so skipping...: %s", dcm)
            continue
        parsed = parse_result_txt(result_file)
        kind = classify_rt_image(dcm, params)
        items.append(_item_from_result(dcm, parsed, kind == "MV"))

    if not items:
        logger.warning("no Winston-Lutz results in %s", case_dir)
        return items

    json_dir = machine_root / "Data" / "Json"
    if not json_dir.is_dir():
        json_dir = machine_root / "Data" / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    save_items_json(items, json_dir / f"{case_dir.name}.json")

    csv_file = str((machine_cfg or {}).get("record_csv_file") or "")
    if not csv_file and param:
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
            _gtc_csv(max_item, params),
        )
        csv_path = Path(csv_file)
        try:
            with csv_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            logger.warning("could not append csv %s: %s", csv_path, exc)

    tmpl_root = report_template_root(machine_cfg) if machine_cfg else None
    if tmpl_root is None:
        tmpl_root = machine_root / "ReportTmplt"
    tol = 1.0
    if machine_cfg and machine_cfg.get("WL_pass_tolerance") not in (None, ""):
        tol = float(machine_cfg["WL_pass_tolerance"])
    elif param:
        tol = param.get_float("WL_pass_tolerance", 1.0) or 1.0
    if tmpl_root.is_dir():
        n_fail = sum(
            1 for i in items if i.calc_norm_of_bb_offset_from_field_center() > tol
        )
        _render_reports(
            case_dir,
            items,
            tmpl_root,
            tol,
            params,
            case_passed=n_fail == 0
            and not case_has_missing_required_ri(case_dir, machine_cfg),
        )

    if send_email and param is not None:
        short = case_dir / "report.short.html"
        if short.is_file():
            send_from_config(param, f"IGRT ({machine})", short.read_text(encoding="utf-8"))

    return items


def case_recency_key(folder: str | Path) -> tuple:
    """Sort key: newer case folders compare greater (use reverse=True for newest first)."""
    path = Path(folder)
    try:
        return (1, datetime.strptime(path.name, "%y-%m-%d_%H-%M-%S").timestamp())
    except ValueError:
        pass
    try:
        return (0, path.stat().st_mtime)
    except OSError:
        return (0, 0.0)


def list_ri_files(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    files = sorted(folder.glob("RI.*.dcm"))
    if not files:
        files = sorted(
            p for p in folder.glob("*.dcm") if p.name.upper().startswith("RI.")
        )
    return files


def list_case_candidates(machine: dict) -> list[Path]:
    """Case-named directories under DATA_FOLDER (no RI or result scan)."""
    data = Path(str(machine.get("DATA_FOLDER") or "")).expanduser()
    if not data.is_dir():
        return []
    pattern = str(machine.get("CASE_FOLDER_NAME_REGEX") or "").strip() or DEFAULT_CASE_FOLDER_REGEX
    found: list[Path] = []
    try:
        with os.scandir(data) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and re.fullmatch(pattern, entry.name):
                    found.append(Path(entry.path))
    except OSError:
        return []
    found.sort(key=case_recency_key, reverse=True)
    return found


def case_has_ri(folder: str | Path) -> bool:
    folder = Path(folder)
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                upper = name.upper()
                if upper.startswith("RI.") and upper.endswith(".DCM"):
                    return True
    except OSError:
        return False
    return False


def case_has_results(folder: str | Path) -> bool:
    folder = Path(folder)
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and entry.name.endswith("_out"):
                    if (Path(entry.path) / "result.txt").is_file():
                        return True
    except OSError:
        return False
    return False


def list_case_folders(machine: dict) -> list[Path]:
    """Case directories under DATA_FOLDER that match the name regex and contain RI DICOMs."""
    return [p for p in list_case_candidates(machine) if case_has_ri(p)]


def case_has_missing_required_ri(folder: str | Path, machine: dict | None = None) -> bool:
    """True when ALL_RI_IMAGE_REQUIRED and a required plan beam has no matching RI."""
    folder = Path(folder)
    machine = machine or find_machine_for_folder(folder)
    if not machine_all_ri_required(machine):
        return False
    plan = find_machine_rtplan(machine)
    if plan is None:
        return False
    try:
        beams = list_plan_beams(plan)
    except Exception:
        return False
    matched, _unmatched = match_ri_files_to_beams(list_ri_files(folder), beams)
    return bool(
        missing_required_beams(beams, matched, machine_ignore_beams(machine))
    )


def case_result_status(folder: str | Path, tol_mm: float = 1.0) -> str:
    """Return 'new', 'pass', or 'fail' from existing result.txt files."""
    items = load_existing_results(folder)
    if not items:
        return "new"
    if any(item.calc_norm_of_bb_offset_from_field_center() > tol_mm for item in items):
        return "fail"
    machine = find_machine_for_folder(folder)
    if machine_all_ri_required(machine) and case_has_missing_required_ri(folder, machine):
        return "fail"
    return "pass"


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
    """Return (mv_method, kv_method) from result.txt, then MACHINES JSON, then config.txt, then log.txt."""
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
        machine_cfg = find_machine_for_folder(folder)
        if machine_cfg:
            if not mv:
                mv = normalize_bb_method(str(machine_cfg.get("MV_bb_search_method") or ""))
            if not kv:
                kv = normalize_bb_method(str(machine_cfg.get("kV_bb_search_method") or ""))

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
    params: AnalysisParams | None = None,
) -> list[WinstonLutzItem]:
    """Analyze every RI DICOM in a folder. Does not require the YY-MM-DD case name."""
    folder = Path(folder)
    params = params or analysis_params_from_machine(find_machine_for_folder(folder))
    items: list[WinstonLutzItem] = []
    for dcm in list_ri_files(folder):
        kind = classify_rt_image(dcm, params)
        out_dir = analyze_ri(dcm, mv_method, kv_method, preprocess=preprocess, params=params)
        result_file = (out_dir / "result.txt") if out_dir else None
        if result_file is None or not result_file.is_file():
            logger.info("result file not found... so skipping...: %s", dcm)
            continue
        parsed = parse_result_txt(result_file)
        items.append(_item_from_result(dcm, parsed, kind == "MV"))
    if write_reports and items:
        write_html_reports(folder, items)
    return items
