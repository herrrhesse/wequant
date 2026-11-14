"""S4.8: what WEEK and MONTH accept inside them, checked when the expression is built."""

import pytest

from wequant.expr import CS_RANK, MA, MONTH, WEEK, Codes, ExprError, Field, from_form


@pytest.mark.parametrize(
    "name",
    ["st", "st_known", "limit_up", "limit_up_price", "list_days", "delisting", "member@000300.SH", "board@main",
     "exchange@SH", "factor"],
)  # fmt: skip
def test_a_period_reads_only_what_bars_have(name):
    """Prices, volume and amount, adjusted or not; the error points at the field inside the period."""
    with pytest.raises(ExprError) as refused:
        from_form(["WEEK", ["MAX", "close", name]])
    assert (refused.value.code, refused.value.path) == ("period_field", (1, 2))


def test_a_period_reads_no_list_of_stocks():
    with pytest.raises(ExprError, match="CODES") as refused:
        MONTH(Codes(["600000.SH"]))
    assert refused.value.code == "period_field"


@pytest.mark.parametrize("inner", [WEEK, MONTH])
def test_periods_do_not_nest(inner):
    with pytest.raises(ExprError) as refused:
        WEEK(MA(inner(Field("close")), 3))
    assert (refused.value.code, refused.value.path) == ("period_nested", (1, 1))


def test_a_period_holds_no_cross_sectional_operator():
    """A rank is taken within a decision day's pool, which bars do not have; it goes outside: CS_RANK(WEEK(...))."""
    with pytest.raises(ExprError) as refused:
        WEEK(CS_RANK(Field("adj_close")))
    assert refused.value.code == "period_cross_section"
    assert CS_RANK(WEEK(Field("adj_close"))).kind == "num"


def test_a_period_keeps_its_argument_kind():
    assert (WEEK(Field("close") > 10).kind, MONTH(MA(Field("adj_close"), 3)).kind) == ("bool", "num")
