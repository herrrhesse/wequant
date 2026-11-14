"""S4.2's simulated holdings on the synthetic market (tests.service.conftest.MARKET): 600000.SH opens at 20.00 every
day but the last and closes at 20.00 + 0.50 t on day t."""

import pytest

from tests.service.conftest import DAYS
from wequant.service import Context
from wequant.service.backtest import BacktestRequest, backtest
from wequant.service.explain import ExplainRequest, explain
from wequant.service.screen import ScreenRequest, screen

ONLY_A = {  # holds 600000.SH alone, sold by an 8% take profit
    "factors": [{"name": "level", "expr": "close"}],
    "top_n": 1,
    "period": "day",
    "pool": {"stocks": {"codes": ["600000.SH"]}},
    "sell": {"mode": "rules", "take_profit": 0.08},
}


def test_the_simulated_holdings_say_what_each_rule_makes_of_them(market_home):
    """Bought on day 1 at 20.00, taken profit at day 4's close (22.00, +10%), sold on day 5, bought again on day 6 at
    20.00: day 6 closes at 23.00, +15%, so the take profit marks it to sell and no new target is chosen."""
    request = ScreenRequest(strategy=ONLY_A, date=DAYS[6], simulate={"start": DAYS[0]})
    found = screen(request, Context(market_home))
    (row,) = found.holdings.rows
    assert (row.code, row.cost, row.close, row.hold_days, row.action, row.cause) == (
        "600000.SH", 20.0, 23.0, 1, "sell", "take_profit"
    )  # fmt: skip
    check = row.rules[0]
    assert (check.rule, check.triggered, check.value, check.threshold) == (
        "take_profit",
        True,
        pytest.approx(0.15),
        0.08,
    )
    assert [r.role for r in found.rows] == ["none"] and found.holdings.note.startswith("模拟持仓：从 2024-01-02 起")


def test_explaining_a_run_with_sell_rules_gives_each_rules_state_on_the_holding(market_home):
    """The archived run, played again to day 6: the same holding, its take profit set off at that close."""
    run_id = backtest(BacktestRequest(strategy=ONLY_A, benchmark=None), Context(market_home)).run_id
    found = explain(ExplainRequest(run=run_id, code="600000.SH", date=DAYS[6]), Context(market_home))
    assert [(r.rule, r.triggered, r.value) for r in found.rules] == [("take_profit", True, pytest.approx(0.15))]


def test_a_strategy_selling_by_its_rules_alone_warns_when_screened_without_holdings(market_home):
    found = screen(ScreenRequest(strategy=ONLY_A, date=DAYS[6]), Context(market_home))
    assert found.holdings is None and "screen_without_holdings" in [w.code for w in found.warnings]
