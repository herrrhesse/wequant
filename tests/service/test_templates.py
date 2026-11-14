"""Strategy templates and the indicator list (round 3, S3.8). Synthetic market only (tests.service.conftest)."""

import datetime as dt

import pytest

from tests.service.conftest import DAYS
from wequant import service
from wequant.data.indexes import OFFERED
from wequant.expr import FAMILIES, FIELDS, PLANNED, ExprError, from_form
from wequant.service import Context
from wequant.service.indicators import FIELD_TEXT
from wequant.service.templates import TEMPLATES
from wequant.strategy import Strategy

D = [dt.date.fromisoformat(day) for day in DAYS]


@pytest.mark.parametrize("form", [entry[2] for entry in TEMPLATES.values()], ids=TEMPLATES)
def test_every_template_is_a_valid_strategy_on_an_offered_scope(form):
    pool = Strategy.from_form(form).pool
    assert pool.stocks is None and pool.index in (None, *OFFERED)


@pytest.mark.parametrize("id", TEMPLATES)
def test_every_template_backtests(market_home, id):
    template = next(t for t in service.strategy_templates(service.TemplatesRequest()).rows if t.id == id)
    run = service.backtest(service.BacktestRequest(strategy=template.strategy, save=False), Context(market_home))
    assert run.run_days == len(DAYS) - 1


def test_the_indicators_name_each_field_the_expressions_read_or_plan_once():
    families = {f"{family}@{param}" for family, spec in FAMILIES.items() for param in spec.params}
    assert sorted(FIELD_TEXT) == sorted({*FIELDS, *families, *PLANNED})


def test_an_available_indicator_passes_the_expression_check_and_a_planned_one_is_refused():
    for row in service.expr_indicators(service.IndicatorsRequest()).rows:
        if row.available:
            assert from_form(row.form).kind == row.kind, row.id
        else:
            with pytest.raises(ExprError) as raised:
                from_form(row.form)
            assert raised.value.code == "not_available" and row.reason


def test_an_indicator_s_days_are_the_days_its_data_has(market_home):
    """The market has bars on every day and no daily_status rows; a 5-day change needs 5 earlier bars."""
    rows = {row.id: row for row in service.expr_indicators(service.IndicatorsRequest(), Context(market_home)).rows}
    assert (rows["close"].first, rows["close"].last) == (D[0], D[-1])
    assert (rows["st"].first, rows["st"].last) == (None, None)
    assert (rows["change_5"].first, rows["change_5"].last) == (D[5], D[-1])
