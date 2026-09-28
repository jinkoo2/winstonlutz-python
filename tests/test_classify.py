from pathlib import Path

from winstonlutz.analysis import MV_KVP_MIN, classify_rt_image, read_kvp, read_ri_setup
from winstonlutz.pipeline import list_ri_files

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "Edge" / "Data" / "26-09-25_06-15-37"
NEW_SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "Edge" / "Data" / "26-09-24_06-13-24"


def test_energy_threshold():
    assert 85 < MV_KVP_MIN <= 6000


def test_sample_kvp_classifies_mv_and_kv():
    if not SAMPLE.is_dir():
        return
    kinds = set()
    for dcm in SAMPLE.glob("RI.*.dcm"):
        kvp = read_kvp(dcm)
        kind = classify_rt_image(dcm)
        assert kvp is not None
        assert kind in ("MV", "kV")
        if kvp >= MV_KVP_MIN:
            assert kind == "MV"
        else:
            assert kind == "kV"
        kinds.add(kind)
    assert kinds == {"MV", "kV"}


def test_read_ri_setup_fills_geometry_without_results():
    if not NEW_SAMPLE.is_dir():
        return
    files = list_ri_files(NEW_SAMPLE)
    assert files
    kinds = set()
    for dcm in files:
        setup = read_ri_setup(dcm)
        assert setup.kind in ("MV", "kV")
        kinds.add(setup.kind)
        assert abs(setup.gantry) <= 360.0
        assert abs(setup.table) <= 360.0
        assert abs(setup.collimator) <= 360.0
    assert kinds == {"MV", "kV"}


def test_read_ri_setup_matches_existing_result_txt():
    if not SAMPLE.is_dir():
        return
    from winstonlutz.analysis import parse_result_txt

    dcm = next(SAMPLE.glob("RI.*.dcm"), None)
    if dcm is None:
        return
    result = dcm.parent / (dcm.name + "_out") / "result.txt"
    if not result.is_file():
        return
    parsed = parse_result_txt(result)
    setup = read_ri_setup(dcm)
    assert abs(setup.gantry - float(parsed["gantry"])) < 1e-4
    assert abs(setup.table - float(parsed["table"])) < 1e-4
    assert abs(setup.collimator - float(parsed["collimator"])) < 1e-4
