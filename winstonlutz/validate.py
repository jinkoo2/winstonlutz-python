"""Compare Python analysis to golden result.txt from sample_data."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from .analysis import analyze_image, parse_result_txt

logger = logging.getLogger(__name__)


def _method_from_log(log_file: Path) -> tuple[str, str]:
    field = "field_search_yes"
    bb = "bb_search_ConnectedComponent"
    if not log_file.is_file():
        return field, bb
    text = log_file.read_text(encoding="utf-8", errors="replace")
    if "bb search method = bb_search_LoG" in text:
        bb = "bb_search_LoG"
    elif "bb search method = bb_search_OtsuThreshold" in text:
        bb = "bb_search_OtsuThreshold"
    elif "bb search method = bb_search_ConnectedComponent" in text:
        bb = "bb_search_ConnectedComponent"
    if "img.field.mask.mhd" not in text and "otsu thresholding for radiation field detection" not in text:
        field = "field_search_no"
    return field, bb


def _vec_err(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) < 2 or len(b) < 2:
        return float("inf")
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def validate_sample_data(sample_root: str | Path, tol_mm: float = 0.1, limit: int = 0) -> bool:
    sample_root = Path(sample_root)
    pairs = []
    for result_txt in sample_root.rglob("result.txt"):
        out_dir = result_txt.parent
        if not out_dir.name.endswith(".dcm_out"):
            continue
        dcm = out_dir.parent / out_dir.name[: -len("_out")]
        if dcm.is_file():
            pairs.append((dcm, result_txt))
    pairs.sort()
    if limit:
        pairs = pairs[:limit]

    failed = 0
    compared = 0
    for dcm, golden in pairs:
        field, bb = _method_from_log(golden.parent / "log.txt")
        if not (golden.parent / "img.field.mask.mhd").is_file():
            field = "field_search_no"
        gold = parse_result_txt(golden)
        with tempfile.TemporaryDirectory(prefix="wl_val_") as tmp:
            try:
                result = analyze_image(
                    dcm,
                    tmp,
                    field_search=field,
                    bb_search=bb,
                    preprocess=False,
                    write_debug=False,
                )
            except Exception as exc:
                logger.error("FAIL %s: %s", dcm.name, exc)
                failed += 1
                continue
        err_fc = _vec_err(result.field_center, gold.get("field center") or [])
        err_bb = _vec_err(result.bb_center, gold.get("bb_cetner") or [])
        err_off = _vec_err(result.bb_offset, gold.get("bb offset") or [])
        compared += 1
        worst = max(err_fc, err_bb, err_off)
        status = "OK" if worst <= tol_mm else "FAIL"
        if status == "FAIL":
            failed += 1
        print(
            f"{status:4} {dcm.parent.parent.parent.name}/{dcm.parent.name}/{dcm.name}  "
            f"fc={err_fc:.4f} bb={err_bb:.4f} off={err_off:.4f} mm  [{field} {bb}]"
        )
    print(f"compared {compared} images, failed {failed}, tol={tol_mm} mm")
    return failed == 0 and compared > 0
