"""Result records, matching WinstonLutzItem."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path


def calc_norm(v: list[float] | tuple[float, ...] | None) -> float:
    if not v:
        return 0.0
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


@dataclass
class WinstonLutzItem:
    gantry: float = 0.0
    table: float = 0.0
    collimator: float = 0.0
    field_center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    bb_center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    bb_offset_from_field_center: list[float] = field(default_factory=lambda: [0.0, 0.0])
    DCM: str = ""
    user: str = ""
    MV: bool = True
    sid_mm: float = 1500.0
    bb_method: str = ""

    def calc_norm_of_bb_offset_from_field_center(self) -> float:
        return calc_norm(self.bb_offset_from_field_center)

    def get_machine_param_string(self, delim: str = ";") -> str:
        return f"G={self.gantry:.0f}{delim}T={self.table:.0f}{delim}C={self.collimator:.0f}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WinstonLutzItem":
        return cls(
            gantry=float(data.get("gantry") or 0),
            table=float(data.get("table") or 0),
            collimator=float(data.get("collimator") or 0),
            field_center=list(data.get("field_center") or [0.0, 0.0]),
            bb_center=list(data.get("bb_center") or [0.0, 0.0]),
            bb_offset_from_field_center=list(
                data.get("bb_offset_from_field_center") or [0.0, 0.0]
            ),
            DCM=data.get("DCM") or "",
            user=data.get("user") or "",
            MV=bool(data.get("MV", True)),
            sid_mm=float(data.get("sid_mm") or 1500.0),
            bb_method=data.get("bb_method") or data.get("bb_search") or "",
        )


def save_items_json(items: list[WinstonLutzItem], json_file: str | Path) -> None:
    path = Path(json_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([i.to_dict() for i in items], indent=2), encoding="utf-8")


def load_items_json(json_file: str | Path) -> list[WinstonLutzItem]:
    path = Path(json_file)
    if not path.is_file():
        raise FileNotFoundError(f"Json file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [WinstonLutzItem.from_dict(row) for row in data]
