"""S4.1: a refused formula names the line and columns of the words at fault (columns count characters, from 1; the end
column is just after the last one)."""

import pytest

from wequant.formula import FormulaError, compile_formula


@pytest.mark.parametrize(
    ("text", "code", "place"),
    [
        ("XG: C>MA(C,5)\nAND MACD(C)>0", "formula_unknown_name", (2, 5, 2, 9, "MACD")),
        ("{line 1\nline 2}\nXG: C>ZIG(C,5);", "future_function", (3, 7, 3, 10, "ZIG")),
        ("{中文注释}XG: C>FOO(1);", "formula_unknown_name", (1, 13, 1, 16, "FOO")),
        ("XG: MA(C, V)>1", "formula_not_constant", (1, 11, 1, 12, "V")),
        ("XG: MA(C,0)>1", "out_of_range", (1, 10, 1, 11, "MA")),
        ("A:=C>O;\nXG:A AND (C>O;", "formula_syntax", (2, 14, 2, 15, ";")),
        ("XG: C>O {never closed", "formula_syntax", (1, 9, 1, 10, "{")),
        ("XG: C>O # 1", "formula_syntax", (1, 9, 1, 10, "#")),
        ("XG: C>", "formula_syntax", (1, 7, 1, 7, None)),
        ("A:=1;\nB:=2;", "formula_no_output", (2, 1, 2, 2, None)),
    ],
)
def test_an_error_names_its_place_in_the_text(text, code, place):
    with pytest.raises(FormulaError) as refused:
        compile_formula(text)
    found = refused.value.position
    assert (refused.value.code, tuple(found[k] for k in ("line", "col", "end_line", "end_col", "token"))) == (
        code,
        place,
    )


def test_a_missing_parenthesis_names_the_one_left_open():
    with pytest.raises(FormulaError, match=r"line 2, col 10"):
        compile_formula("A:=C>O;\nXG:A AND (C>O;")
