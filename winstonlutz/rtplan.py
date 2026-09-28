"""Read Varian RT Plan (RP.*.dcm) beam geometry for Winston-Lutz."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .analysis import wrap_deg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanBeam:
    number: int
    name: str
    kind: str  # "MV" or "kV"
    gantry: float
    table: float
    collimator: float
    delivery: str = ""
    energy: str = ""


def _float(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).split("\\")[0].strip())
    except (TypeError, ValueError):
        return default


def _first_cp(beam):
    cps = list(getattr(beam, "ControlPointSequence", None) or [])
    return cps[0] if cps else None


def _imaging_tokens(beam) -> list[str]:
    tokens: list[str] = []
    for item in getattr(beam, "PlannedVerificationImageSequence", None) or []:
        raw = getattr(item, "ImagingDeviceSpecificAcquisitionParameters", None)
        if raw in (None, ""):
            continue
        if isinstance(raw, (list, tuple)):
            tokens.extend(str(x) for x in raw)
        else:
            tokens.append(str(raw))
    return [t.upper() for t in tokens]


def _beam_kind(beam) -> str:
    tokens = _imaging_tokens(beam)
    joined = " ".join(tokens)
    if "KV" in joined or "CBCT" in joined:
        return "kV"
    delivery = str(getattr(beam, "TreatmentDeliveryType", "") or "").upper()
    if delivery == "TREATMENT" or "PORT" in joined:
        return "MV"
    if delivery == "SETUP":
        return "kV"
    return "MV"


def _table_from_couch(couch) -> float:
    if couch in (None, ""):
        return 0.0
    return wrap_deg(360.0 - _float(couch))


def plan_cache_path(rtplan: str | Path) -> Path:
    """Sidecar JSON: ``RP.xxx.dcm.json`` next to the RT Plan."""
    path = Path(rtplan)
    return path.with_name(path.name + ".json")


def _beam_from_dict(data: dict) -> PlanBeam:
    return PlanBeam(
        number=int(data.get("number") or 0),
        name=str(data.get("name") or "").strip(),
        kind=str(data.get("kind") or "MV").strip() or "MV",
        gantry=float(data.get("gantry") or 0),
        table=float(data.get("table") or 0),
        collimator=float(data.get("collimator") or 0),
        delivery=str(data.get("delivery") or "").strip(),
        energy=str(data.get("energy") or "").strip(),
    )


def _plan_cache_is_current(rtplan: Path, cache: Path) -> bool:
    if not cache.is_file():
        return False
    try:
        return cache.stat().st_mtime >= rtplan.stat().st_mtime
    except OSError:
        return False


def _load_plan_cache(cache: Path) -> list[PlanBeam] | None:
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    raw = data.get("beams") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return None
    beams: list[PlanBeam] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            beams.append(_beam_from_dict(item))
        except (TypeError, ValueError):
            return None
    beams.sort(key=lambda b: b.number)
    return beams


def _write_plan_cache(cache: Path, beams: list[PlanBeam]) -> None:
    payload = {"beams": [asdict(b) for b in beams]}
    try:
        cache.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("could not write RT Plan cache %s", cache)


def _read_rtplan_dicom(rtplan: Path) -> list[PlanBeam]:
    import pydicom

    ds = pydicom.dcmread(str(rtplan), stop_before_pixels=True)
    beams: list[PlanBeam] = []
    for beam in getattr(ds, "BeamSequence", None) or []:
        cp = _first_cp(beam)
        couch = getattr(cp, "PatientSupportAngle", None) if cp is not None else None
        energy = ""
        if cp is not None and getattr(cp, "NominalBeamEnergy", None) not in (None, ""):
            energy = str(cp.NominalBeamEnergy).strip()
        beams.append(
            PlanBeam(
                number=int(_float(getattr(beam, "BeamNumber", 0))),
                name=str(getattr(beam, "BeamName", "") or "").strip(),
                kind=_beam_kind(beam),
                gantry=wrap_deg(_float(getattr(cp, "GantryAngle", 0) if cp is not None else 0)),
                table=_table_from_couch(couch),
                collimator=wrap_deg(
                    _float(getattr(cp, "BeamLimitingDeviceAngle", 0) if cp is not None else 0)
                ),
                delivery=str(getattr(beam, "TreatmentDeliveryType", "") or "").strip(),
                energy=energy,
            )
        )
    beams.sort(key=lambda b: b.number)
    return beams


def list_plan_beams(rtplan: str | Path) -> list[PlanBeam]:
    """Beams from an RT Plan. Uses ``RP*.dcm.json`` when it is as new as the DICOM."""
    path = Path(rtplan)
    cache = plan_cache_path(path)
    if _plan_cache_is_current(path, cache):
        cached = _load_plan_cache(cache)
        if cached is not None:
            return cached
    beams = _read_rtplan_dicom(path)
    _write_plan_cache(cache, beams)
    return beams


def find_machine_rtplan(machine: dict | None) -> Path | None:
    """RP*.dcm: DICOM_PLAN_FILE, PLAN_FOLDER, or <DATA_FOLDER>/../Plan."""
    if not machine:
        return None
    explicit = str(
        machine.get("DICOM_PLAN_FILE")
        or machine.get("RTPLAN_FILE_PATH")
        or ""
    ).strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    folders: list[Path] = []
    plan_folder = str(machine.get("PLAN_FOLDER") or "").strip()
    if plan_folder:
        folders.append(Path(plan_folder).expanduser())
    data = str(machine.get("DATA_FOLDER") or "").strip()
    if data:
        folders.append(Path(data).expanduser().parent / "Plan")
    found: list[Path] = []
    for folder in folders:
        if folder.is_file() and folder.name.upper().startswith("RP"):
            found.append(folder)
            continue
        if not folder.is_dir():
            continue
        found.extend(sorted(folder.glob("RP*.dcm")))
        found.extend(sorted(p for p in folder.glob("*.dcm") if p.name.upper().startswith("RP.")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        key = path.resolve() if path.exists() else path
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            unique.append(path)
    return unique[0] if unique else None


def referenced_beam_number(dcm: str | Path) -> int | None:
    """``ReferencedBeamNumber`` (300C,0006) from an RI DICOM, if present."""
    import pydicom

    ds = pydicom.dcmread(str(dcm), stop_before_pixels=True)
    raw = getattr(ds, "ReferencedBeamNumber", None)
    if raw in (None, ""):
        return None
    try:
        return int(float(str(raw).split("\\")[0].strip()))
    except (TypeError, ValueError):
        return None


def match_ri_files_to_beams(
    ri_files: list[Path],
    beams: list[PlanBeam],
) -> tuple[dict[int, Path], list[Path]]:
    """Map plan beam number → first matching RI. Extra or unnumbered RIs are unmatched."""
    numbers = {b.number for b in beams}
    matched: dict[int, Path] = {}
    unmatched: list[Path] = []
    for path in ri_files:
        try:
            n = referenced_beam_number(path)
        except Exception:
            n = None
        if n is None or n not in numbers:
            unmatched.append(path)
            continue
        if n in matched:
            unmatched.append(path)
            continue
        matched[n] = path
    return matched, unmatched


def beam_expects_image(beam: PlanBeam, ignore_beams: set[int] | None = None) -> bool:
    """False for IGNORE_BEAMS numbers and CBCT setup fields (listed, not required)."""
    if ignore_beams and beam.number in ignore_beams:
        return False
    return "CBCT" not in (beam.name or "").upper()


def missing_required_beams(
    beams: list[PlanBeam],
    matched: dict[int, Path],
    ignore_beams: set[int] | None = None,
) -> list[PlanBeam]:
    return [
        b
        for b in beams
        if beam_expects_image(b, ignore_beams) and b.number not in matched
    ]


def machine_ignore_beams(machine: dict | None) -> set[int]:
    """Beam numbers from IGNORE_BEAMS (list or comma-separated). Empty if omitted."""
    if not machine:
        return set()
    raw = machine.get("IGNORE_BEAMS")
    if raw in (None, ""):
        return set()
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = str(raw).replace(";", ",").split(",")
    out: set[int] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            out.add(int(float(text)))
        except (TypeError, ValueError):
            continue
    return out


def machine_all_ri_required(machine: dict | None) -> bool:
    if not machine:
        return False
    value = machine.get("ALL_RI_IMAGE_REQUIRED")
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")
