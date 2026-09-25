from pathlib import Path

from winstonlutz.analysis import MV_KVP_MIN, classify_rt_image, read_kvp

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "Edge" / "Data" / "26-09-25_06-15-37"


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
