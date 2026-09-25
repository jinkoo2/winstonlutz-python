"""SimpleITK port of projects-cpp/imagetools/2d/winston_lutz_2d.cxx.

The production exe that created sample_data logs read -> crop -> field/BB
search (no median/normalize/invert). That path is the default. Set
``preprocess=True`` to also run the median / rescale / invert steps from
the current C++ source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import SimpleITK as sitk

logger = logging.getLogger(__name__)

CROP_MM = 50.0
DEFAULT_SID_MM = 1500.0
# Varian stores 6 MV as KVP=6000. Diagnostic kV images are typically 70–140.
MV_KVP_MIN = 1000.0


class AnalysisSkip(Exception):
    """DICOM did not match the requested tag criteria."""


def read_kvp(dcm_file: str | Path) -> float | None:
    """Read beam energy (kV) from ExposureSequence or top-level KVP."""
    import pydicom

    ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
    candidates = []
    if getattr(ds, "ExposureSequence", None):
        for item in ds.ExposureSequence:
            if getattr(item, "KVP", None) not in (None, ""):
                candidates.append(item.KVP)
    if getattr(ds, "KVP", None) not in (None, ""):
        candidates.append(ds.KVP)
    for raw in candidates:
        try:
            return float(str(raw).split("\\")[0].strip())
        except ValueError:
            continue
    return None


def classify_rt_image(dcm_file: str | Path) -> str | None:
    """Return ``MV`` or ``kV``. Prefer energy; fall back to label/description, then size."""
    import pydicom

    kvp = read_kvp(dcm_file)
    if kvp is not None:
        kind = "MV" if kvp >= MV_KVP_MIN else "kV"
        logger.info("classified %s as %s from KVP=%.0f", Path(dcm_file).name, kind, kvp)
        return kind

    ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
    desc = str(getattr(ds, "RTImageDescription", "") or "").upper()
    label = str(getattr(ds, "RTImageLabel", "") or "").upper()
    if "[MV]" in desc or label.startswith("MV"):
        logger.info("classified %s as MV from RT image label/description", Path(dcm_file).name)
        return "MV"
    if "[KV]" in desc or label.startswith("KV"):
        logger.info("classified %s as kV from RT image label/description", Path(dcm_file).name)
        return "kV"

    rows = int(getattr(ds, "Rows", 0) or 0)
    cols = int(getattr(ds, "Columns", 0) or 0)
    if rows == 1190 and cols == 1190:
        logger.info("classified %s as MV from image size %sx%s", Path(dcm_file).name, cols, rows)
        return "MV"
    if rows == 768 and cols == 1024:
        logger.info("classified %s as kV from image size %sx%s", Path(dcm_file).name, cols, rows)
        return "kV"
    logger.info("could not classify %s (no energy, unrecognized size %sx%s)", Path(dcm_file).name, cols, rows)
    return None


def normalize_bb_method(name: str) -> str:
    """Map stored/log names to GUI values: ConnectedComponent, LoG, OtsuThreshold."""
    text = (name or "").strip()
    if text.lower().startswith("bb_search_"):
        text = text[10:]
    aliases = {
        "connectedcomponent": "ConnectedComponent",
        "confidenceconnected": "ConnectedComponent",
        "log": "LoG",
        "otsuthreshold": "OtsuThreshold",
        "otsu": "OtsuThreshold",
    }
    return aliases.get(text.lower(), text)


def bb_method_from_log(log_file: str | Path) -> str:
    path = Path(log_file)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if "bb_search_LoG" in text:
        return "LoG"
    if "bb_search_OtsuThreshold" in text:
        return "OtsuThreshold"
    if "bb_search_ConnectedComponent" in text or "bb_search_ConfidenceConnected" in text:
        return "ConnectedComponent"
    return ""


@dataclass
class AnalysisResult:
    sid_mm: float
    operator: str
    gantry: float
    table: float
    collimator: float
    field_center: list[float]
    bb_center: list[float]
    bb_offset: list[float]
    field_center_image: list[float] = field(default_factory=list)
    bb_center_image: list[float] = field(default_factory=list)
    mv: bool = True
    out_dir: str = ""
    bb_search: str = ""


def _as_2d(image: sitk.Image) -> sitk.Image:
    if image.GetDimension() == 3 and image.GetSize()[2] == 1:
        image = image[:, :, 0]
    if image.GetDimension() != 2:
        raise ValueError(f"Expected a 2D image, got dimension {image.GetDimension()}")
    return sitk.Cast(image, sitk.sitkFloat32)


def _set_origin_to_center(image: sitk.Image) -> sitk.Image:
    size = image.GetSize()
    spacing = image.GetSpacing()
    origin = (-(size[0] * spacing[0] / 2.0), -(size[1] * spacing[1] / 2.0))
    image.SetOrigin(origin)
    return image


def _tag(reader: sitk.ImageFileReader, key: str) -> str:
    try:
        if reader.HasMetaDataKey(key):
            return reader.GetMetaData(key).strip()
    except Exception:
        return ""
    return ""


def _parse_float(text: str, default: float = 0.0) -> float:
    if not text:
        return default
    try:
        return float(text.split("\\")[0].strip())
    except ValueError:
        return default


def _match_criteria(reader: sitk.ImageFileReader, criteria: str) -> bool:
    if not criteria:
        return True
    for part in criteria.split("&"):
        criterion = part.strip()
        if not criterion:
            continue
        if "=" not in criterion:
            continue
        tag, expected = [s.strip() for s in criterion.split("=", 1)]
        actual = _tag(reader, tag)
        if actual == "":
            logger.info("there is no value in the dicom file for %s", tag)
            return False
        if actual != expected:
            logger.info(
                "the value in the file (%s) is different from the given condition value (%s).",
                actual,
                expected,
            )
            return False
    return True


def _center_of_mass(mask: sitk.Image) -> list[float]:
    arr = sitk.GetArrayFromImage(mask)
    # SimpleITK array is [y, x]
    ys, xs = np.nonzero(arr != 0)
    icount = int(xs.size)
    dx = 0.0
    dy = 0.0
    if icount > 1:
        dx = float(xs.mean())
        dy = float(ys.mean())
    spacing = mask.GetSpacing()
    origin = mask.GetOrigin()
    dx = dx * spacing[0] + origin[0]
    dy = dy * spacing[1] + origin[1]
    return [dx, dy]


def _crop_center(image: sitk.Image, crop_mm: float = CROP_MM) -> sitk.Image:
    spacing = image.GetSpacing()
    size = image.GetSize()
    width = int(crop_mm / spacing[0])
    height = int(crop_mm / spacing[1])
    if width % 2 != 0:
        width += 1
    if height % 2 != 0:
        height += 1
    start_x = size[0] // 2 - width // 2
    start_y = size[1] // 2 - height // 2
    logger.info(
        "cropping to the image center: index=[%d,%d],size=[%d,%d]",
        start_x,
        start_y,
        width,
        height,
    )
    cropped = sitk.RegionOfInterest(image, [width, height], [start_x, start_y])
    return _set_origin_to_center(cropped)


def _is_field_intensity_low(image: sitk.Image) -> bool:
    binary = sitk.BinaryThreshold(image, -1e24, 127, 0, 255)
    arr = sitk.GetArrayFromImage(binary)
    count_high = int(np.count_nonzero(arr > 127))
    count_low = int(arr.size - count_high)
    return count_low < count_high


def _preprocess(image: sitk.Image) -> sitk.Image:
    image = sitk.Median(image, [2, 2])
    image = sitk.RescaleIntensity(image, 0, 255)
    if _is_field_intensity_low(image):
        logger.info("The radiation field pixel value is low--> inverting the image")
        image = sitk.InvertIntensity(image, 255)
    return image


def _otsu_mask(image: sitk.Image) -> sitk.Image:
    logger.info("otsu thresholding for radiation field detection")
    return sitk.OtsuThreshold(image, 0, 255)


def _bb_log(image: sitk.Image, field_mask: sitk.Image) -> tuple[sitk.Image, list[float]]:
    eroded = sitk.BinaryErode(field_mask, [10, 10], sitk.sitkBall, 0.0, 255.0)
    laplacian = sitk.LaplacianRecursiveGaussian(image, 1.0, True)
    masked = sitk.Mask(laplacian, eroded, 0.0)
    stats = sitk.MinimumMaximumImageFilter()
    stats.Execute(masked)
    maximum = stats.GetMaximum()
    bb_mask = sitk.BinaryThreshold(masked, maximum / 2.0, maximum, 255, 0)
    return bb_mask, _center_of_mass(bb_mask)


def _physical_to_index(image: sitk.Image, point: list[float]) -> tuple[int, int]:
    origin = image.GetOrigin()
    spacing = image.GetSpacing()
    x = int((point[0] - origin[0]) / spacing[0])
    y = int((point[1] - origin[1]) / spacing[1])
    size = image.GetSize()
    x = min(max(x, 0), size[0] - 1)
    y = min(max(y, 0), size[1] - 1)
    return x, y


def _bb_confidence(image: sitk.Image, seed_phys: list[float]) -> tuple[sitk.Image, list[float]]:
    last = list(seed_phys)
    bb_mask = None
    bb_center = list(seed_phys)
    for _ in range(10):
        seed = _physical_to_index(image, last)
        bb_mask = sitk.ConfidenceConnected(image, [seed], 5, 2.5, 2, 255)
        bb_center = _center_of_mass(bb_mask)
        dist = ((last[0] - bb_center[0]) ** 2 + (last[1] - bb_center[1]) ** 2) ** 0.5
        if dist < 0.01:
            break
        last = list(bb_center)
    assert bb_mask is not None
    return bb_mask, bb_center


def _bb_otsu(image: sitk.Image) -> tuple[sitk.Image, list[float]]:
    mask = _otsu_mask(image)
    return mask, _center_of_mass(mask)


def _draw_cross(rgb: np.ndarray, image: sitk.Image, point: list[float], color: tuple[int, int, int]) -> None:
    origin = image.GetOrigin()
    spacing = image.GetSpacing()
    x = int((point[0] - origin[0]) / spacing[0])
    y = int((point[1] - origin[1]) / spacing[1])
    h, w = rgb.shape[:2]
    for i in range(-2, 3):
        xx, yy = x + i, y
        if 0 <= xx < w and 0 <= yy < h:
            rgb[yy, xx] = color
        xx, yy = x, y + i
        if 0 <= xx < w and 0 <= yy < h:
            rgb[yy, xx] = color


def prepare_display_image(image: sitk.Image, style: str = "rescale") -> sitk.Image:
    """Build the 8-bit image used in reports.

    ``rescale`` matches Python ``result.png`` / C++ ``result2.png``: crop is
    assumed already applied, then min-max stretch to 0–255.
    ``log`` matches C++ ``result.png``: Laplacian-of-Gaussian (σ=1), then stretch.
    """
    image = _as_2d(image)
    if style == "log":
        image = sitk.LaplacianRecursiveGaussian(image, 1.0, True)
    return sitk.Cast(sitk.RescaleIntensity(image, 0, 255), sitk.sitkUInt8)


def load_ri_for_display(dcm_file: str | Path, style: str = "rescale") -> sitk.Image:
    """Load an RI DICOM the same way analysis does, then make a display image.

    ``full`` keeps the whole frame (min-max 8-bit). ``rescale`` and ``log``
    crop 50 mm about the image center first, like the report.
    """
    reader = sitk.ImageFileReader()
    reader.SetImageIO("GDCMImageIO")
    reader.SetFileName(str(dcm_file))
    image = _set_origin_to_center(_as_2d(reader.Execute()))
    if style == "full":
        return prepare_display_image(image, "rescale")
    cropped = _crop_center(image)
    return prepare_display_image(cropped, style)


def _save_overlay(image: sitk.Image, field_center: list[float], bb_center: list[float], path: Path) -> None:
    gray = prepare_display_image(image, "rescale")
    arr = sitk.GetArrayFromImage(gray)
    rgb = np.stack([arr, arr, arr], axis=-1)
    _draw_cross(rgb, image, field_center, (255, 0, 0))
    _draw_cross(rgb, image, bb_center, (0, 255, 0))
    out = sitk.GetImageFromArray(rgb, isVector=True)
    out.SetOrigin(image.GetOrigin())
    out.SetSpacing(image.GetSpacing())
    sitk.WriteImage(out, str(path))


def write_result_txt(result: AnalysisResult, path: Path) -> None:
    lines = [
        f"SID_mm={result.sid_mm}",
        f"Operator={result.operator}",
        f"Gantry={result.gantry}",
        f"Table={result.table}",
        f"Collimator={result.collimator}",
        f"bb_search={normalize_bb_method(result.bb_search)}",
        f"field center={result.field_center[0]},{result.field_center[1]}",
        f"bb_cetner={result.bb_center[0]},{result.bb_center[1]}",
        f"bb offset={result.bb_offset[0]},{result.bb_offset[1]}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_result_txt(path: Path) -> dict:
    data: dict = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in ("field center", "bb_cetner", "bb offset"):
            parts = [p.strip() for p in value.split(",") if p.strip() != ""]
            data[key] = [float(p) for p in parts]
        elif key in ("gantry", "table", "collimator", "sid_mm"):
            data[key] = float(value)
        elif key in ("bb_search", "bb_method"):
            data["bb_search"] = normalize_bb_method(value)
        else:
            data[key] = value
    return data


def analyze_image(
    dcm_file: str | Path,
    out_dir: str | Path | None = None,
    field_search: str = "field_search_yes",
    bb_search: str = "bb_search_ConnectedComponent",
    match_criteria: str = "",
    preprocess: bool = False,
    write_debug: bool = True,
) -> AnalysisResult:
    """Run Winston-Lutz analysis on one RI DICOM.

    ``bb_search`` is ``bb_search_LoG``, ``bb_search_ConnectedComponent``,
    or ``bb_search_OtsuThreshold`` (the ``bb_search_`` prefix is optional).
    """
    dcm_file = Path(dcm_file)
    if out_dir is None:
        out_dir = Path(str(dcm_file) + "_out")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    method = bb_search.replace("bb_search_", "")
    do_field = field_search == "field_search_yes"

    reader = sitk.ImageFileReader()
    reader.SetImageIO("GDCMImageIO")
    reader.SetFileName(str(dcm_file))
    reader.LoadPrivateTagsOn()
    image = _as_2d(reader.Execute())
    image = _set_origin_to_center(image)

    if not _match_criteria(reader, match_criteria):
        raise AnalysisSkip(f"DICOM tag criteria not matched: {match_criteria}")

    gantry = _parse_float(_tag(reader, "300a|011e"))
    col = _parse_float(_tag(reader, "300a|0120"))
    couch = _parse_float(_tag(reader, "300a|0122"))
    table = 360.0 - couch if _tag(reader, "300a|0122") else 0.0
    sid_mm = _parse_float(_tag(reader, "3002|0026"), DEFAULT_SID_MM)
    operator = _tag(reader, "0008|1070")

    if write_debug:
        sitk.WriteImage(image, str(out_dir / "img0.mhd"))

    if preprocess:
        image = _preprocess(image)

    image = _crop_center(image)
    if write_debug:
        sitk.WriteImage(image, str(out_dir / "img1.mhd"))

    field_mask = None
    if do_field:
        field_mask = _otsu_mask(image)
        field_center = _center_of_mass(field_mask)
        if write_debug:
            sitk.WriteImage(field_mask, str(out_dir / "img.field.mask.mhd"))
    else:
        field_center = [0.0, 0.0]

    logger.info("bb search method = bb_search_%s", method)
    if method.lower() == "log":
        if field_mask is None:
            field_mask = sitk.Image(image.GetSize(), sitk.sitkUInt8)
            field_mask.CopyInformation(image)
            field_mask = sitk.Add(field_mask, 255)
        bb_mask, bb_center = _bb_log(image, field_mask)
    elif method.lower() in ("connectedcomponent", "confidenceconnected"):
        seed = field_center if do_field else [0.0, 0.0]
        bb_mask, bb_center = _bb_confidence(image, seed)
    else:
        bb_mask, bb_center = _bb_otsu(image)

    if write_debug:
        sitk.WriteImage(bb_mask, str(out_dir / "img.bb.mask.mhd"))

    diff = [bb_center[0] - field_center[0], bb_center[1] - field_center[1]]
    scale = 1000.0 / sid_mm if sid_mm else 1.0
    field_iso = [field_center[0] * scale, field_center[1] * scale]
    bb_iso = [bb_center[0] * scale, bb_center[1] * scale]
    diff_iso = [diff[0] * scale, diff[1] * scale]

    if write_debug:
        _save_overlay(image, field_center, bb_center, out_dir / "result.png")

    result = AnalysisResult(
        sid_mm=sid_mm,
        operator=operator,
        gantry=gantry,
        table=table,
        collimator=col,
        field_center=field_iso,
        bb_center=bb_iso,
        bb_offset=diff_iso,
        field_center_image=field_center,
        bb_center_image=bb_center,
        mv=do_field,
        out_dir=str(out_dir),
        bb_search=normalize_bb_method(method),
    )
    write_result_txt(result, out_dir / "result.txt")
    return result
