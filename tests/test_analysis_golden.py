from pathlib import Path

from winstonlutz.validate import validate_sample_data

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"


def test_sample_data_matches_golden():
    assert SAMPLE.is_dir()
    assert validate_sample_data(SAMPLE, tol_mm=0.1)
