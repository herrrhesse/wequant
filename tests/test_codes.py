import pytest

from wequant.data import codes


def test_same_digits_on_different_exchanges_are_different_types():
    assert codes.security_type("000001.SH") == "index"
    assert codes.security_type("000001.SZ") == "stock"


@pytest.mark.parametrize(
    "code, expected",
    [
        ("000300.SH", "index"),
        ("399300.SZ", "index"),
        ("899050.BJ", "index"),
        ("510300.SH", "fund"),
        ("159915.SZ", "fund"),
        ("600519.SH", "stock"),
        ("688981.SH", "stock"),
        ("430047.BJ", "stock"),
    ],
)
def test_security_type(code, expected):
    assert codes.security_type(code) == expected


@pytest.mark.parametrize("code", ["900901.SH", "200002.SZ", "110001.SH", "123456.BJ"])
def test_unknown_prefixes_raise_instead_of_guessing(code):
    with pytest.raises(ValueError):
        codes.security_type(code)


@pytest.mark.parametrize(
    "code, board",
    [
        ("600000.SH", "main"),
        ("601988.SH", "main"),
        ("603259.SH", "main"),
        ("605499.SH", "main"),
        ("688981.SH", "star"),
        ("689009.SH", "star"),
        ("000001.SZ", "main"),
        ("001979.SZ", "main"),
        ("002594.SZ", "main"),
        ("003816.SZ", "main"),
        ("300750.SZ", "chinext"),
        ("301269.SZ", "chinext"),
        ("302132.SZ", "chinext"),
        ("430047.BJ", "bse"),
        ("830799.BJ", "bse"),
        ("920047.BJ", "bse"),
    ],
)
def test_board(code, board):
    assert codes.board(code) == board


def test_bse_code_era():
    assert codes.bse_code_era("430047.BJ") == "old"
    assert codes.bse_code_era("873223.BJ") == "old"
    assert codes.bse_code_era("920047.BJ") == "new"
    with pytest.raises(ValueError):
        codes.bse_code_era("600000.SH")
