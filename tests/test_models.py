from pathlib import Path

from winstonlutz.analysis import parse_result_txt
from winstonlutz.models import WinstonLutzItem, calc_norm
from winstonlutz.pipeline import validate_case_dir_name


def test_calc_norm():
    assert abs(calc_norm([3.0, 4.0]) - 5.0) < 1e-9


def test_machine_param_string():
    item = WinstonLutzItem(gantry=90, table=0, collimator=360)
    assert item.get_machine_param_string(";") == "G=90;T=0;C=360"


def test_case_dir_name_ok():
    validate_case_dir_name(Path("26-09-25_07-58-11"))


def test_case_dir_name_bad():
    try:
        validate_case_dir_name(Path("not-a-case"))
        assert False
    except ValueError:
        pass


def test_parse_result_txt(tmp_path):
    p = tmp_path / "result.txt"
    p.write_text(
        "SID_mm=1500.01\nOperator=ab\nGantry=90\nTable=0\nCollimator=0\n"
        "bb_search=LoG\n"
        "field center=0.1,-0.2\nbb_cetner=0.0,0.0\nbb offset=-0.1,0.2\n",
        encoding="utf-8",
    )
    data = parse_result_txt(p)
    assert data["operator"] == "ab"
    assert data["bb_cetner"] == [0.0, 0.0]
    assert data["field center"] == [0.1, -0.2]
    assert data["bb_search"] == "LoG"
