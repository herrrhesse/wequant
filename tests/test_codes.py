import pytest

from wequant.data import codes


def test_same_digits_on_different_exchanges_are_different_types():
    assert codes.security_type("000001.SH") == "index"
    assert codes.security_type("000001.SZ") == "stock"


@pytest.mark.parametrize(
    "found, expected",
    [
        pytest.param(("510300.SH", "159915.SZ"), "fund", id="510300.SH-fund"),  # SH 5xxxxx and SZ 15xxxx
        pytest.param(("899050.BJ",), "index", id="899050.BJ-index"),
    ],
)
def test_security_type(found, expected):
    assert {codes.security_type(code) for code in found} == {expected}


@pytest.mark.parametrize("code", ["900901.SH"])
def test_unknown_prefixes_raise_instead_of_guessing(code):
    with pytest.raises(ValueError):
        codes.security_type(code)


@pytest.mark.parametrize(
    "code, board",
    [
        ("600000.SH", "main"),
        ("688981.SH", "star"),
        ("002594.SZ", "main"),  # the SME board until 2021
        ("300750.SZ", "chinext"),
        ("920047.BJ", "bse"),
    ],
)
def test_board(code, board):
    assert codes.board(code) == board


def test_a_code_keeps_its_exchange_suffix():
    """A wequant code is six digits, a dot and an upper-case exchange; nothing else is read as one."""
    assert codes.split("600000.SH") == ("600000", "SH")
    for bad in ("600000", "600000.sh", "SH600000", "sh.600000", "600000.SS", "60000.SH", "600000.SH "):
        with pytest.raises(ValueError):
            codes.split(bad)
