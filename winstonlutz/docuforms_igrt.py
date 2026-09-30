"""Upload one Winston-Lutz case to DocuForms2 (upload_igrt post-processing)."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .app_settings import form_id_for_machine
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
    """Case time as YY-MM-DD_HH-MM-SS, or Date/Time from report.html if present."""
    case_dir = Path(case_dir)
    for name in ("report.html", "report.short.html"):
        path = case_dir / name
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read(16000)
        except OSError:
            continue
        match = re.search(
            r"Date/Time</span>\s*<input[^>]*\bvalue=\"([^\"]+)\"",
            text,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1).strip()
        for fmt in ("%y/%m/%d %H:%M:%S", "%y/%m/%d %H:%M"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    try:
        return datetime.strptime(case_dir.name, "%y-%m-%d_%H-%M-%S")
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
    """Convert the full report.html to PDF (WeasyPrint, else Chrome/Edge)."""
    html_path = Path(html_path)
    dest = Path(dest)
    if not html_path.is_file():
        return False
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("could not read %s for PDF", html_path)
        return False
    text = text.replace(".\\", "./").replace("\\", "/")
    tmp_html = html_path.parent / f".{html_path.stem}.pdfsrc.html"
    local_dir = None
    try:
        tmp_html.write_text(text, encoding="utf-8")
        if _try_write_pdf(tmp_html, dest):
            return True
        local_html, local_dir = _local_pdf_workspace(html_path, text)
        if local_html is not None and _try_write_pdf(local_html, dest):
            return True
    except Exception:
        logger.warning("report.pdf conversion failed", exc_info=True)
    finally:
        try:
            tmp_html.unlink(missing_ok=True)
        except OSError:
            pass
        if local_dir is not None:
            shutil.rmtree(local_dir, ignore_errors=True)
    logger.warning("report.pdf conversion failed for %s", html_path)
    return False


def _try_write_pdf(html_path: Path, dest: Path) -> bool:
    if not (_pdf_weasyprint(html_path, dest) or _pdf_chromium(html_path, dest)):
        return False
    size = dest.stat().st_size if dest.is_file() else 0
    if size <= 0:
        return False
    logger.info("created report.pdf (%s KB)", f"{size / 1024:.1f}")
    return True


def _local_pdf_workspace(html_path: Path, rewritten_html: str) -> tuple[Path | None, Path | None]:
    """Copy rewritten HTML and result.png files to a local temp dir (UNC-safe)."""
    try:
        work = Path(tempfile.mkdtemp(prefix="wl_pdf_"))
        dest_html = work / html_path.name
        dest_html.write_text(rewritten_html, encoding="utf-8")
        for png in html_path.parent.glob("*_out/result.png"):
            out = work / png.parent.name
            out.mkdir(exist_ok=True)
            shutil.copy2(png, out / "result.png")
        return dest_html, work
    except OSError:
        logger.warning("could not copy report assets for PDF", exc_info=True)
        return None, None


def _pdf_weasyprint(html_path: Path, dest: Path) -> bool:
    try:
        from weasyprint import HTML as WeasyHTML
    except ImportError:
        return False
    try:
        WeasyHTML(string=html_path.read_text(encoding="utf-8"), base_url=str(html_path.parent)).write_pdf(str(dest))
    except Exception:
        logger.warning("weasyprint PDF failed", exc_info=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def _chromium_exe() -> str | None:
    roots = (
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    )
    rels = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )
    for root in roots:
        if not root:
            continue
        for rel in rels:
            path = Path(root) / rel
            if path.is_file():
                return str(path)
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def _pdf_chromium(html_path: Path, dest: Path) -> bool:
    exe = _chromium_exe()
    if not exe:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            dest.unlink()
        except OSError:
            return False
    cmd = [
        exe,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={dest}",
        html_path.resolve().as_uri(),
    ]
    try:
        proc = subprocess.run(cmd, check=False, timeout=180, capture_output=True)
    except Exception:
        logger.warning("Chrome/Edge PDF conversion failed", exc_info=True)
        return False
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
        logger.warning("Chrome/Edge PDF conversion failed: %s", err or proc.returncode)
        return False
    return True


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


def notify_docuforms_event(
    step: dict | None,
    status: str,
    case_dir: Path,
    machine_cfg: dict | None,
    extra: dict | None = None,
) -> None:
    """Email success or failure lists for this DocuForms2 step."""
    from .emailer import send_list_email

    step = step or {}
    if status in ("ok", "dry-run"):
        to = step.get("email_success_event_to")
    elif status == "failed":
        to = step.get("email_failure_event_to")
    else:
        return
    machine = str((machine_cfg or {}).get("NAME") or "").strip()
    label = f"{machine}/{case_dir.name}" if machine else case_dir.name
    lines = [
        f"status={status}",
        f"machine={machine}",
        f"case={case_dir.name}",
        f"path={case_dir}",
    ]
    for key, value in (extra or {}).items():
        if value is None or str(value).strip() == "":
            continue
        lines.append(f"{key}={value}")
    send_list_email(
        to,
        f"DocuForms2 {status}: {label}",
        "\n".join(lines),
        context="postprocess.docuforms2_igrt",
        subject=f"Winston-Lutz DocuForms2 {status}: {label}"[:180],
        blocking=True,
    )


def upload_case(
    case_dir: str | Path,
    items: list[WinstonLutzItem],
    machine_cfg: dict | None,
    step: dict,
) -> str:
    """Upload one case. Returns 'ok', 'skipped', or 'dry-run'. Raises on hard failure."""
    case_dir = Path(case_dir)
    step = step or {}
    form_id = form_id_for_machine(step, machine_cfg)
    backend = str(step.get("backend_url") or "").strip().rstrip("/")
    if not form_id:
        logger.info("DocuForms2 skipped: no form_id for this machine")
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no form_id"}
        )
        return "skipped"
    if not backend:
        logger.info("DocuForms2 skipped: empty backend_url")
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "empty backend_url"}
        )
        return "skipped"
    if not items:
        logger.info("DocuForms2 skipped: no analysis items in %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no analysis items"}
        )
        return "skipped"
    resubmit = bool(step.get("resubmit", False))
    if already_imported(case_dir) and not resubmit:
        logger.info("DocuForms2 skipped (already imported): %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "already imported"}
        )
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
        if not dry_run and bool(step.get("attach_pdf", True)):
            report = case_dir / "report.html"
            if not report.is_file():
                raise RuntimeError(f"report.html missing in {case_dir}; cannot attach report.pdf")
            tmp_pdf = Path(tempfile.gettempdir()) / f"wl_pdf_{case_dir.name}_{uuid.uuid4().hex}.pdf"
            if not html_to_pdf(report, tmp_pdf):
                raise RuntimeError("could not convert report.html to report.pdf")
            case_pdf = case_dir / "report.pdf"
            try:
                shutil.copy2(tmp_pdf, case_pdf)
            except OSError:
                logger.warning("could not copy report.pdf into %s", case_dir)
            info = upload_file(
                backend, tmp_pdf, "report.pdf", timeout=timeout, verify=verify
            )
            if not info:
                raise RuntimeError("report.pdf upload failed")
            attachments.append(info)
        if dry_run:
            logger.info(
                "DocuForms2 dry-run %s form=%s fields=%s result=%s",
                case_dir.name,
                form_id,
                len(values),
                result,
            )
            notify_docuforms_event(
                step,
                "dry-run",
                case_dir,
                machine_cfg,
                {"form_id": form_id, "backend_url": backend, "result": result},
            )
            return "dry-run"
        response = submit_form(
            backend,
            form_id,
            values,
            metadata,
            result,
            attachments,
            timeout=timeout,
            verify=verify,
        )
        submission_id = str((response or {}).get("_id") or (response or {}).get("id") or "")
        write_marker(
            case_dir,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "attachments": [
                    a.get("originalName") for a in attachments if a.get("originalName")
                ],
            },
        )
        logger.info(
            "DocuForms2 submitted %s to %s result=%s performed_at=%s id=%s",
            case_dir.name,
            form_id,
            result,
            values.get("performed_at") or "",
            submission_id,
        )
        notify_docuforms_event(
            step,
            "ok",
            case_dir,
            machine_cfg,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "attachments": ",".join(
                    a.get("originalName") or "" for a in attachments
                ),
            },
        )
        return "ok"
    finally:
        for path in (tmp_zip, tmp_pdf):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
