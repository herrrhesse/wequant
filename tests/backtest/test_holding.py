"""S4.2: the sell rules on a two-stock market, expectations worked out by hand from its prices.

A (600000.SH) closes above B (600004.SH, always 10.00) unless a test says otherwise, so a strategy ranking by close
picks A. The strategy decides every day; a decision at day t's close trades at day t + 1's open, A bought at 20.00
on day 1 unless a test says otherwise.
"""

import pytest

from tests.backtest.market import build_store, business_days
from wequant.backtest import Backtest, panel_for
from wequant.backtest import run as run_backtest
from wequant.backtest.engine import run as rebalance
from wequant.backtest.holding import run as holding
from wequant.runs.causes import with_causes
from wequant.strategy import Strategy

A, B = "600000.SH", "600004.SH"
DAYS = business_days(10)  # 2024-01-02 .. 2024-01-15
FLAT = [(20.0, 20.0)] * 10


def strategy(**sell) -> Strategy:
    form = {"factors": [{"name": "level", "expr": "adj_close"}], "top_n": 1, "period": "day", "pool": {}}
    return Strategy.from_form(form | ({"sell": sell} if sell else {}))


def run(tmp_path, a_bars, factors=(), engine=run_backtest, **sell):
    store = build_store(tmp_path / "home", DAYS, {A: a_bars, B: [(10.0, 10.0)] * 10}, factors=factors)
    backtest = Backtest(strategy(**sell), benchmark=None)
    return engine(backtest, panel_for(backtest, store=store, cache_dir=tmp_path / "cache"))


def trades(found) -> list[tuple]:
    """(day index, code, side, cause) of each filled order."""
    rows = with_causes(found).filter(found.ledger.orders["shares"] > 0)
    return [(DAYS.index(str(r["date"])), r["code"], r["side"], r["cause"]) for r in rows.iter_rows(named=True)]


def path(*closes: float) -> list[tuple]:
    """A's bars: 20.00 on days 0 and 1, then these closes from day 2, each day opening at the close before."""
    bars, close = [(20.0, 20.0), (20.0, 20.0)], 20.0
    for c in closes:
        bars.append((close, c))
        close = c
    return bars + [(close, close)] * (10 - len(bars))


def test_a_stop_loss_sells_the_day_after_the_close_falls_that_far_below_cost(tmp_path):
    """Cost 20.00; day 3 closes at 17.80, 11% below (the stop is 10%): sold at day 4's open, B bought in its slot."""
    found = run(tmp_path, path(19.0, 17.8), mode="rules", stop_loss=0.1)
    assert trades(found) == [(1, A, "buy", "enter"), (4, A, "sell", "stop_loss"), (4, B, "buy", "enter")]
    exit_ = found.exits.row(0, named=True)
    assert (exit_["rule"], exit_["value"], exit_["cost"], exit_["close"]) == (
        "stop_loss",
        pytest.approx(0.11),
        20.0,
        17.8,
    )


EDGES = [  # a close exactly at the rule's level; the factor makes the back-adjusted prices' rounding lean away from it
    ("stop_loss", 0.1, 1.615, [(10.0, 10.0), (10.0, 9.0)]),  # bought at 10.00, closes at 9.00 the same day: -10%
    ("take_profit", 0.1, 1.007, [(10.0, 10.0), (10.0, 11.0)]),  # bought at 10.00, closes at 11.00: +10%
    ("trailing_stop", 0.1, 1.615, [(10.0, 10.0), (10.0, 10.0), (10.0, 9.0)]),  # its highest close 10.00, then 9.00
]


@pytest.mark.parametrize(("rule", "level", "factor", "head"), EDGES, ids=[edge[0] for edge in EDGES])
def test_a_close_exactly_at_a_rules_level_sets_it_off(tmp_path, rule, level, factor, head):
    """The coordinator's case: an A-share limit down is exactly -10%, so a 10% stop meets it at the level itself."""
    bars = head + [head[-1][1:] * 2] * (10 - len(head))
    found = run(tmp_path, bars, factors=[(A, DAYS[0], factor)], mode="rules", **{rule: level})
    assert [(r["rule"], DAYS.index(str(r["date"]))) for r in found.exits.iter_rows(named=True)][:1] == [
        (rule, len(head) - 1)
    ]


def test_a_take_profit_sells_the_day_after_the_close_rises_that_far_above_cost(tmp_path):
    found = run(tmp_path, path(21.0, 22.2), mode="rules", take_profit=0.1)  # +11% on day 3
    assert (4, A, "sell", "take_profit") in trades(found)


def test_a_trailing_stop_sells_from_the_highest_close_since_bought(tmp_path):
    """Highest close 22.00 (day 2); day 3 closes at 20.80, 5.5% below it though still above cost."""
    found = run(tmp_path, path(22.0, 20.8), mode="rules", trailing_stop=0.05)
    assert (4, A, "sell", "trailing_stop") in trades(found)
    assert found.exits.row(0, named=True)["peak"] == 22.0


def test_a_holding_period_counts_the_first_fill_day_as_one(tmp_path):
    """Bought on day 1, held 3 days at day 3's close: sold on day 4."""
    found = run(tmp_path, FLAT, mode="rules", max_hold_days=3)
    assert trades(found)[:2] == [(1, A, "buy", "enter"), (4, A, "sell", "max_hold")]


def test_an_ex_date_does_not_set_off_a_stop(tmp_path):
    """A pays out on day 3, its factor doubling: the unadjusted close halves (20.00 to 9.50), the back-adjusted one
    falls 5%, under the 10% stop."""
    bars = FLAT[:3] + [(9.5, 9.5, 10.0)] + [(9.5, 9.5)] * 6
    found = run(tmp_path, bars, factors=[(A, DAYS[0], 1.0), (A, DAYS[3], 2.0)], mode="rules", stop_loss=0.1)
    assert [t for t in trades(found) if t[2] == "sell"] == [] and found.exits.height == 0


def test_a_sale_that_cannot_fill_is_tried_again_the_next_day(tmp_path):
    """Day 4 opens and closes at A's down limit (16.02, high = low): the stop's sale is refused, then fills on day 5,
    caused by the stop marked on day 3."""
    bars = path(19.0, 17.8)
    bars[4], bars[5] = (16.02, 16.02), (16.02, 16.5)
    found = run(tmp_path, bars, mode="rules", stop_loss=0.1)
    refused = found.ledger.orders.filter((found.ledger.orders["side"] == "sell") & (found.ledger.orders["shares"] == 0))
    assert [DAYS.index(str(d)) for d in refused["date"]] == [4] and (5, A, "sell", "stop_loss") in trades(found)
    assert [(str(d), str(f)) for d, f in found.exits.select("date", "first").iter_rows()] == [
        (DAYS[3], DAYS[3]),
        (DAYS[4], DAYS[3]),
    ]


def test_a_minimum_holding_holds_back_a_rebalance_sale_only(tmp_path):
    """A falls below B on day 1 (9.00): a rebalance would sell it on day 2, but held fewer than 3 days it stays until
    day 3's close."""
    bars = [(20.0, 20.0), (20.0, 9.0)] + [(9.0, 9.0)] * 8
    found = run(tmp_path, bars, min_hold_days=3)
    assert [t[:3] for t in trades(found)][:3] == [(1, A, "buy"), (4, A, "sell"), (4, B, "buy")]


@pytest.mark.parametrize(("cooldown", "back"), [(0, 5), (2, 7)])
def test_a_stock_sold_waits_its_cooldown_before_it_is_bought_again(tmp_path, cooldown, back):
    """The stop sells A on day 4; it still ranks first, so the next rebalance buys it back unless it is cooling down
    (through day 4 + cooldown)."""
    found = run(tmp_path, path(19.0, 17.8), stop_loss=0.1, cooldown_days=cooldown)
    bought = [day for day, code, side, _ in trades(found) if code == A and side == "buy"]
    assert bought[:2] == [1, back]


def test_the_keep_formula_holds_back_the_sell_formula_and_by_default_not_the_stops(tmp_path):
    """The sell formula holds from day 2 (close below 20); keep holds while the close is above 18. The stop (10%) sells
    on day 4 in both cases; the sell formula alone waits for keep to end (day 3's close, 17.80)."""
    sell = {"name": "down", "expr": {"text": "RAW_CLOSE<20"}}
    keep = {"name": "high", "expr": {"text": "RAW_CLOSE>18"}}
    by_formula = run(tmp_path / "f", path(19.0, 17.8), mode="rules", sell_when=sell, keep_when=keep)
    assert (4, A, "sell", "sell_formula") in trades(by_formula)
    held = run(tmp_path / "k", path(19.0, 17.8, 17.0), mode="rules", stop_loss=0.1, keep_when=keep, keep_over_stops=True,
               max_hold_days=9)  # fmt: skip
    assert (4, A, "sell", "stop_loss") in trades(held)  # keep ended at day 3's close (17.80 is not above 18)


def test_both_engines_trade_a_strategy_without_sell_rules_alike(tmp_path):
    """The holding engine on a strategy the rebalance engine can run: the same orders and equity, row for row."""
    one, two = (
        run(tmp_path / "1", path(19.0, 9.0), engine=rebalance),
        run(tmp_path / "2", path(19.0, 9.0), engine=holding),
    )
    assert one.ledger.orders.equals(two.ledger.orders) and one.ledger.daily.equals(two.ledger.daily)
    assert two.exits.height == 0
