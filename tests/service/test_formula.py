"""S4.1's operations on the synthetic market (tests.service.conftest.MARKET): formula check, trial, format and vocab,
and a formula inside a strategy request."""

import datetime as dt

import pytest

from tests.service.conftest import DAYS, SCREENED
from wequant.formula import compile_formula
from wequant.formula.format import TEXT_OF
from wequant.formula.names import FAMILY_FUNCTIONS, FIELD_NAMES, FUNCTIONS, PERIOD_FUNCTIONS
from wequant.service import Context, ServiceError
from wequant.service.formula import (
    FormatRequest,
    FormulaRequest,
    TrialRequest,
    formula_check,
    formula_format,
    formula_trial,
)
from wequant.service.formula_vocab import ALIASES, FIELD_TEXT, FUNCTION_TEXT, VocabRequest, formula_vocab
from wequant.service.strategies import StrategyRequest, strategy_validate

CODE = "000001.SZ"  # opens at 10.00 and closes at 10.00 + 0.20 t on day t, 1,000,000 shares a day


def test_a_formula_that_compiles_comes_back_with_its_tree_and_lines():
    found = formula_check(FormulaRequest(text="N:=3; XG: C>MA(C,N);", period="week", role="condition"))
    assert (found.ok, found.output, found.kind, found.errors) == (True, "XG", "bool", [])
    assert found.key == compile_formula("C>MA(C,3)", "week").expr.key
    assert [(v.name, v.kind, v.value, v.output) for v in found.variables] == [
        ("N", "const", 3.0, False),
        ("XG", "bool", None, True),
    ]


def test_a_refused_formula_comes_back_with_its_place_and_a_chinese_hint():
    """Not a failure: the editor checks as the user types."""
    found = formula_check(FormulaRequest(text="XG: C>MA(C,5)\nAND MACD(C)>0"))
    issue = found.errors[0]
    assert (found.ok, issue.code, issue.line, issue.col, issue.end_col, issue.token) == (
        False, "formula_unknown_name", 2, 5, 9, "MACD"
    )  # fmt: skip
    assert issue.hint.startswith("MACD 是指标，不是函数")


def test_a_back_adjusted_price_against_a_constant_is_a_warning_with_the_raw_name_to_use():
    found = formula_check(FormulaRequest(text="C>5"))
    assert found.ok and [(w.code, "RAW_CLOSE" in w.hint) for w in found.warnings] == [("adjusted_level", True)]


def test_a_strategy_with_a_refused_formula_names_the_formula_and_the_place(context):
    form = {"filters": [{"name": "kdj", "expr": {"text": "XG: C>REFX(C,1);", "period": "week"}}]}
    with pytest.raises(ServiceError) as refused:
        strategy_validate(StrategyRequest(strategy=form), context)
    detail = refused.value.detail[0]
    assert (detail.code, detail.loc) == ("future_function", ["strategy", "filters", 0, "expr"])
    assert (detail.position.line, detail.position.col, detail.position.token) == (1, 7, "REFX")
    assert detail.hint.startswith("REFX 是未来函数")


def test_a_daily_trial_shows_each_line_on_each_bar(market_home):
    """A is the day's close over the day before's; XG is 1 when it rose. Prices as the formula reads them, VOL in
    lots, the forward-adjusted price equal to the close where nothing was ex-dated."""
    found = formula_trial(TrialRequest(text="A:=C/REF(C,1); XG: A>1;", code=CODE), Context(market_home))
    closes = [10.0 + 0.2 * t for t in range(len(DAYS))]
    assert [c.name for c in found.columns] == ["A", "XG"] and len(found.rows) == len(DAYS)
    last = found.rows[-1]
    assert last.values == {"A": pytest.approx(closes[-1] / closes[-2]), "XG": 1.0} and found.rows[0].values["A"] is None
    assert (last.close, last.volume, last.raw_close, last.tdx.close) == (
        pytest.approx(closes[-1]), 10_000.0, pytest.approx(closes[-1]), pytest.approx(closes[-1])
    )  # fmt: skip


def test_a_weekly_trial_shows_the_ended_weeks_and_names_the_open_one(market_home):
    """The store ends on Thursday 2024-01-11 and no calendar knows Friday: only the week ended 2024-01-05 is a bar."""
    found = formula_trial(TrialRequest(text="XG: C;", period="week", code=CODE), Context(market_home))
    assert [(r.start, r.date, r.values["XG"]) for r in found.rows] == [
        (dt.date(2024, 1, 2), dt.date(2024, 1, 5), pytest.approx(10.6))
    ]
    assert found.open_period == dt.date(2024, 1, 11)


def test_a_trial_of_a_formula_that_does_not_compile_is_a_usage_error_at_its_place(market_home):
    with pytest.raises(ServiceError) as refused:
        formula_trial(TrialRequest(text="XG: C>", code=CODE), Context(market_home))
    assert refused.value.detail[0].position.col == 7


def test_trees_are_written_as_text_and_an_empty_argument_says_why():
    found = formula_format(FormatRequest(forms=[SCREENED["filters"][0]["expr"], ["IF", "st", "close", None]]))
    assert (found.formulas[0].text, found.formulas[0].period) == ("30 > RAW_CLOSE", "day")
    assert found.formulas[1].text is None and found.formulas[1].error.code == "not_a_form"


def test_the_vocabulary_explains_every_function_and_field_a_formula_can_use():
    assert set(FUNCTION_TEXT) | set(ALIASES) == {*FUNCTIONS, *PERIOD_FUNCTIONS, *FAMILY_FUNCTIONS, "CODES"}
    assert {TEXT_OF[expr.key] for expr in FIELD_NAMES.values()} == set(FIELD_TEXT)  # each tree under its text name


def test_every_example_compiles_for_its_role_and_each_role_has_a_default():
    examples = formula_vocab(VocabRequest()).examples
    assert all(compile_formula(e.text, e.period, e.role) for e in examples)
    defaults = {e.role for e in examples if not e.complete}
    assert defaults == {"condition", "factor", "formula", "universe", "sell"}
