"""Upload one Winston-Lutz case to DocuForms2 (upload_igrt post-processing)."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import ssl
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .models import WinstonLutzItem, calc_norm

logger = logging.getLogger(__name__)

MARKER_NAME = ".docuforms2_igrt.json"
GANTRY_TARGETS = (0, 90, 180, 270)
TABLE_TARGETS = (0, 50, 90, 270, 310, 350, 360)


def round_gantry(g: int) -> int:
    if g > 345:
        g = 0
    return min(
        GANTRY_TARGETS,
        key=lambda x: min(abs(g - x), abs(g - x + 360), abs(g - x - 360)),
    )


def round_table(t: int) -> int:
    """Snap couch angle to a form target. 350 is a real station; only 360 aliases to 0."""
    t = ((int(t) % 360) + 360) % 360
    nearest = min(
        TABLE_TARGETS,
        key=lambda x: min(abs(t - (x % 360)), 360 - abs(t - (x % 360))),
    )
    return 0 if nearest == 360 else nearest


def performed_at_from_case(case_dir: Path) -> datetime | None:
    match = re.match(
        r"(\d{2})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$", case_dir.name
    )
    if not match:
        return None
    try:
        return datetime(
            2000 + int(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
        )
    except ValueError:
        return None


def field_prefix(item: WinstonLutzItem) -> str:
    g = round_gantry(int(round(item.gantry)))
    t = round_table(int(round(item.table)))
    energy = "mv" if item.MV else "kv"
    return f"g{g}_t{t}_{energy}"


def _fmt_offsets(xy: list[float] | tuple[float, ...]) -> tuple[str, str, str]:
    x, y = float(xy[0] if xy else 0), float(xy[1] if len(xy) > 1 else 0)
    return f"{x:.1f}", f"{y:.1f}", f"{calc_norm([x, y]):.1f}"


def pick_unique_items(
    items: list[WinstonLutzItem],
    tol: float,
) -> list[WinstonLutzItem]:
    """One beam per rounded G/T/energy; prefer a Pass when duplicates exist."""
    chosen: dict[str, tuple[WinstonLutzItem, bool]] = {}
    for item in items:
        key = field_prefix(item)
        passed = item.calc_norm_of_bb_offset_from_field_center() <= tol
        prev = chosen.get(key)
        if prev is None or (passed and not prev[1]):
            chosen[key] = (item, passed)
    return [pair[0] for pair in chosen.values()]


def result_png(case_dir: Path, item: WinstonLutzItem) -> Path | None:
    name = Path(item.DCM).name
    if not name:
        return None
    path = case_dir / f"{name}_out" / "result.png"
    return path if path.is_file() else None


def values_from_items(
    case_dir: Path,
    items: list[WinstonLutzItem],
    *,
    tol: float,
    performed_by: str = "",
) -> tuple[dict, str, dict[str, str]]:
    unique = pick_unique_items(items, tol)
    values: dict = {}
    panel_results: dict[str, str] = {}
    stamp = performed_at_from_case(case_dir)
    if stamp:
        values["performed_at"] = stamp.isoformat()
    if performed_by.strip():
        values["performed_by"] = performed_by.strip()

    overall_fail = False
    for item in unique:
        prefix = field_prefix(item)
        d = item.calc_norm_of_bb_offset_from_field_center()
        panel = "PASS" if d <= tol else "FAIL"
        panel_results[prefix] = panel
        if panel == "FAIL":
            overall_fail = True
        png = result_png(case_dir, item)
        if png is not None:
            raw = png.read_bytes()
            values[f"img_{prefix}"] = "data:image/png;base64," + base64.b64encode(raw).decode(
                "ascii"
            )
        x, y, dist = _fmt_offsets(item.bb_offset_from_field_center)
        if item.MV:
            values[f"{prefix}_bb_fc_x"] = x
            values[f"{prefix}_bb_fc_y"] = y
            values[f"{prefix}_bb_fc_d"] = dist
        x, y, dist = _fmt_offsets(item.bb_center)
        values[f"{prefix}_bb_ic_x"] = x
        values[f"{prefix}_bb_ic_y"] = y
        values[f"{prefix}_bb_ic_d"] = dist
        if item.MV:
            x, y, dist = _fmt_offsets(item.field_center)
            values[f"{prefix}_fc_ic_x"] = x
            values[f"{prefix}_fc_ic_y"] = y
            values[f"{prefix}_fc_ic_d"] = dist

    overall = "FAIL" if overall_fail else "PASS" if unique else ""
    return values, overall, panel_results


def build_metadata(
    values: dict,
    overall_result: str,
    panel_results: dict[str, str],
    pass_range: str,
) -> dict:
    metadata = {}
    for field_id in values:
        if field_id.endswith("_d"):
            parts = field_id.rsplit("_", 3)
            prefix = parts[0] if len(parts) >= 4 else ""
            result = ""
            if "_mv_bb_fc_d" in field_id or "_kv_bb_ic_d" in field_id:
                result = panel_results.get(prefix, overall_result)
            metadata[field_id] = {"passRange": pass_range, "result": result}
        else:
            metadata[field_id] = {"result": ""}
    return metadata


def calculate_overall_result(metadata: dict) -> str:
    results = [
        str(meta.get("result") or "").upper()
        for meta in metadata.values()
        if isinstance(meta, dict) and meta.get("result")
    ]
    if not results:
        return ""
    if any(r == "FAIL" for r in results):
        return "FAIL"
    if any(r == "WARNING" for r in results):
        return "WARNING"
    if all(r == "PASS" for r in results):
        return "PASS"
    return ""


def _ssl_context(verify: bool):
    if verify:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _urlopen(req: urllib.request.Request, timeout: float, verify: bool):
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(verify))


def post_json(url: str, payload: dict, *, timeout: float, verify: bool) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def upload_file(
    backend_url: str,
    file_path: Path,
    original_name: str,
    *,
    timeout: float,
    verify: bool,
) -> dict | None:
    raw = file_path.read_bytes()
    boundary = "----WinstonLutz" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{original_name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/api/upload",
        data=header + raw + footer,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("DocuForms2 upload %s failed: %s", original_name, exc)
        return None
    url = str((parsed or {}).get("url") or "")
    if not url:
        return None
    return {"url": url, "originalName": original_name}


def zip_dicoms(case_dir: Path, dest: Path) -> bool:
    files = sorted(p for p in case_dir.glob("*.dcm") if p.is_file())
    if not files:
        return False
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.name)
    return True


def html_to_pdf(html_path: Path, dest: Path) -> bool:
    try:
        from weasyprint import HTML as WeasyHTML
    except ImportError:
        logger.warning("weasyprint not installed; skipping report.pdf")
        return False
    try:
        html = html_path.read_text(encoding="utf-8")
        html = html.replace(".\\", "./").replace("\\", "/")
        WeasyHTML(string=html, base_url=str(html_path.parent)).write_pdf(str(dest))
        return dest.is_file()
    except Exception:
        logger.warning("report.pdf conversion failed", exc_info=True)
        return False


def submit_form(
    backend_url: str,
    form_id: str,
    values: dict,
    metadata: dict,
    result: str,
    attachments: list[dict] | None,
    *,
    timeout: float,
    verify: bool,
) -> dict:
    payload = {
        "values": values,
        "metadata": metadata,
        "result": result,
        "comments": "",
        "submissionHtml": "",
        "attachments": attachments or None,
    }
    return post_json(
        f"{backend_url.rstrip('/')}/api/forms/{form_id}/submit",
        payload,
        timeout=timeout,
        verify=verify,
    )


def already_imported(case_dir: Path) -> bool:
    return (case_dir / MARKER_NAME).is_file()


def write_marker(case_dir: Path, payload: dict) -> None:
    path = case_dir / MARKER_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def upload_case(
    case_dir: str | Path,
    items: list[WinstonLutzItem],
    machine_cfg: dict | None,
    step: dict,
) -> str:
    """Upload one case. Returns 'ok', 'skipped', or 'dry-run'. Raises on hard failure."""
    case_dir = Path(case_dir)
    step = step or {}
    form_id = str((machine_cfg or {}).get("docuforms2_form_id") or "").strip()
    backend = str(step.get("backend_url") or "").strip().rstrip("/")
    if not form_id:
        logger.info("DocuForms2 skipped: no docuforms2_form_id for this machine")
        return "skipped"
    if not backend:
        logger.info("DocuForms2 skipped: empty backend_url")
        return "skipped"
    if not items:
        logger.info("DocuForms2 skipped: no analysis items in %s", case_dir)
        return "skipped"
    resubmit = bool(step.get("resubmit", False))
    if already_imported(case_dir) and not resubmit:
        logger.info("DocuForms2 skipped (already imported): %s", case_dir)
        return "skipped"

    tol = 1.0
    if machine_cfg and machine_cfg.get("WL_pass_tolerance") not in (None, ""):
        tol = float(machine_cfg["WL_pass_tolerance"])
    pass_range = f"0:{tol}"
    performed_by = str(items[0].user or "").strip()
    values, overall, panel_results = values_from_items(
        case_dir, items, tol=tol, performed_by=performed_by
    )
    metadata = build_metadata(values, overall, panel_results, pass_range)
    result = calculate_overall_result(metadata) or overall
    timeout = float(step.get("timeout_sec") or 300)
    verify = bool(step.get("verify_ssl", False))
    dry_run = bool(step.get("dry_run", False))
    attachments: list[dict] = []
    tmp_zip = None
    tmp_pdf = None
    try:
        if not dry_run and bool(step.get("attach_dcm_zip", True)):
            tmp_zip = Path(tempfile.gettempdir()) / f"wl_dcm_{case_dir.name}_{uuid.uuid4().hex}.zip"
            if zip_dicoms(case_dir, tmp_zip):
                info = upload_file(
                    backend, tmp_zip, "input_dcm.zip", timeout=timeout, verify=verify
                )
                if info:
                    attachments.append(info)
        if not dry_run and bool(step.get("attach_pdf", False)):
            report = case_dir / "report.html"
            if report.is_file():
                tmp_pdf = Path(tempfile.gettempdir()) / f"wl_pdf_{case_dir.name}_{uuid.uuid4().hex}.pdf"
                if html_to_pdf(report, tmp_pdf):
                    info = upload_file(
                        backend, tmp_pdf, "report.pdf", timeout=timeout, verify=verify
                    )
                    if info:
                        attachments.append(info)
        if dry_run:
            logger.info(
                "DocuForms2 dry-run %s form=%s fields=%s result=%s",
                case_dir.name,
                form_id,
                len(values),
                result,
            )
            return "dry-run"
        submit_form(
            backend,
            form_id,
            values,
            metadata,
            result,
            attachments,
            timeout=timeout,
            verify=verify,
        )
        write_marker(
            case_dir,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        logger.info("DocuForms2 submitted %s to %s result=%s", case_dir.name, form_id, result)
        return "ok"
    finally:
        for path in (tmp_zip, tmp_pdf):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
