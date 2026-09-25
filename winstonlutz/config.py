"""key=value config files, matching WinstonLutzLib.param."""

from __future__ import annotations

from pathlib import Path


class Param:
    def __init__(self, param_file: str | Path):
        self.file = Path(param_file)

    def get_value(self, key: str) -> str:
        if not self.file.is_file():
            return ""
        key_l = key.strip().lower()
        for raw in self.file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().lower() == key_l:
                return v.strip()
        return ""

    def get_value_as_array(self, key: str) -> list[str]:
        values = self.get_value(key)
        if not values.strip():
            return []
        return [v.strip() for v in values.split(",") if v.strip()]

    def get_float(self, key: str, default: float | None = None) -> float | None:
        text = self.get_value(key)
        if text == "":
            return default
        return float(text)

    def get_bool(self, key: str, default: bool = False) -> bool:
        text = self.get_value(key)
        if text == "":
            return default
        return text.lower() in ("true", "1", "yes")

    def get_int(self, key: str, default: int | None = None) -> int | None:
        text = self.get_value(key)
        if text == "":
            return default
        return int(text)


def find_machine_config(case_dir: Path) -> Path | None:
    """Look for config.txt in the case folder, then each parent (machine folder)."""
    here = Path(case_dir)
    seen: set[Path] = set()
    for path in (here, *here.parents):
        if path in seen:
            break
        seen.add(path)
        candidate = path / "config.txt"
        if candidate.is_file():
            return candidate
    return None
