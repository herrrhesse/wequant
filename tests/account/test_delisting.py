"""The account at a delisting day's open (design 2.4, D17): each guards a rule named in its docstring."""

import datetime as dt
import math

import polars as pl
import pytest

from tests.account.fake import FakePanel
from wequant.account import Account, Execution, delisting_plan, rebalance_orders
from wequant.data.delistings import DELISTINGS

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
NAN = math.nan
EX0 = Execution(slippage_bps=0.0)


def table(*kinds: str, approximate=False, codes=("600000.SH",)) -> pl.DataFrame:
    """DELISTINGS of codes of these kinds, each handled at the open of the last day; other columns null."""
    day = dt.date.fromisoformat(DAYS[-1])
    rows = [
        {"code": c, "delist_date": None if approximate else day, "day": day, "kind": k} for c, k in zip(codes, kinds)
    ]
    frame = pl.DataFrame(rows, schema={name: DELISTINGS[name] for name in ("code", "delist_date", "day", "kind")})
    frame = frame.with_columns(approximate=approximate)
    columns = [pl.col(name) if name in frame.columns else pl.lit(None).alias(name) for name in DELISTINGS]
    return frame.select(columns).cast(DELISTINGS)


def panel(delistings=None, factor=None) -> FakePanel:
    """A last trades days 0 to 2 (closes 10, 10.5, 10.5); B trades every day at 20."""
    open_ = [[10.0, 20.0], [10.0, 20.0], [10.5, 20.0], [NAN, 20.0], [NAN, 20.0]]
    close = [[10.0, 20.0], [10.5, 20.0], [10.5, 20.0], [NAN, 20.0], [NAN, 20.0]]
    return FakePanel(DAYS, ["600000.SH", "000001.SZ"], ["main", "main"], open_, close, factor, delistings=delistings)


def held(fake, execution=EX0, shares=1000) -> Account:
    """An account that bought shares of A on day 1 and closed days 1 to 3."""
    account = Account(fake, 100_000, DAYS[0], execution)
    for t in (1, 2, 3):
        account.begin(t)
        if t == 1:
            account.trade([0], [shares])
        account.end()
    return account


def test_a_write_down_clears_the_holding_at_the_open_and_sizes_the_day_on_what_is_left():
    """Design 2.4: write_down zeroes the shares, leaves the cash, takes the value off today's equity, and records one
    filled sell row without costs (price 0.0, not NaN) and one delisted row; the row is no rebalance order."""
    account = held(panel(table("forced")))
    before, cash = account.equity, account.cash
    account.begin(4)
    assert account.shares.tolist() == [0.0, 0.0] and account.cash == cash
    assert account.equity == pytest.approx(before - 10_500, abs=1e-9)  # 1,000 shares at the last close 10.50
    account.end()
    ledger = account.ledger()
    columns = ("side", "requested", "shares", "price", "value", "commission", "stamp_tax", "transfer_fee", "reason")
    assert ledger.orders.filter(pl.col("date") == dt.date(2024, 1, 8)).select(columns).rows() == [
        ("sell", 1000.0, 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0, "delist_write_down")
    ]
    assert ledger.delisted.rows() == [(dt.date(2024, 1, 8), "600000.SH", "write_down", 1000.0, 10500.0, 0.0)]
    assert rebalance_orders(ledger.orders)["reason"].to_list() == [None]  # only day 1's buy


def test_a_merger_is_cashed_at_its_last_close_without_costs_and_counts_as_sold():
    """D17-2 (random-c9): a merger turns into cash at its last close with no fees; the cash counts in the day's sold."""
    account = held(panel(table("merger")))
    before, cash = account.equity, account.cash
    account.begin(4)
    assert account.cash == pytest.approx(cash + 10_500, abs=1e-9) and account.equity == pytest.approx(before, abs=1e-9)
    account.end()
    assert account.ledger().daily["sold"][-1] == 10_500.0


def test_a_held_treatment_leaves_the_holding():
    """D17: a long suspension is held; nothing is recorded."""
    account = held(panel(table("suspension")))
    account.begin(4)
    account.end()
    assert account.shares.tolist() == [1000.0, 0.0] and account.ledger().delisted.is_empty()


def test_nothing_happens_before_the_delisting_day_though_the_last_bar_is_earlier():
    """Design 2.4 option (a): the day is known only when it comes, never at the last bar."""
    account = Account(panel(table("forced")), 100_000, DAYS[0], EX0)
    for t in range(1, 4):
        account.begin(t)
        if t == 1:
            account.trade([0], [1000])
        assert account.shares[0] == 1000.0  # day 3 has no bar; the delisting is on day 4
        account.end()
    account.begin(4)
    assert account.shares[0] == 0.0


def test_fractional_shares_after_a_late_factor_change_are_cashed_to_the_fen_as_a_sale():
    """Design 2.4: the cash is rounded as Account._sell rounds it; a factor change after the last bar leaves the value."""
    factor = [[1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [1.0035, 1.0], [1.0035, 1.0]]  # an event after A's last bar
    account = held(panel(table("merger"), factor), shares=100)
    account.begin(4)
    account.end()
    exit_ = account.ledger().orders.filter(pl.col("reason") == "delist_exit")
    # 100.35 shares at 10.50 / 1.0035 = 10.463 (10,463 厘): 100 x 10.463 + 0.35 x 10.463 = 3.66205 -> 3.66 yuan
    assert exit_["value"].item() == 1049.96
    assert account.ledger().delisted["value"].item() == pytest.approx(1050.00)  # 100 shares at 10.50


def test_failure_recovery_returns_its_share_of_a_written_down_holding():
    """v4 D17: failure_recovery in [0, 1] is the share of the last close a write-down returns, for sensitivity runs."""
    account = held(panel(table("forced")), Execution(slippage_bps=0.0, failure_recovery=0.25))
    cash = account.cash
    account.begin(4)
    assert account.cash == pytest.approx(cash + 2625, abs=1e-9)  # a quarter of 1,000 x 10.50


def test_the_plan_gives_each_kind_its_treatment_under_each_rule():
    """v4 D17: auto writes down forced and unknown delistings, cashes mergers, holds suspensions; write_down and
    sell_at_last_close treat every delisting alike; hold holds all; an approximate day always writes down to zero."""
    official = table("forced", "merger", "suspension", "unknown", codes=[f"60000{k}.SH" for k in range(4)])
    fake = panel(pl.concat([official, table("merger", approximate=True, codes=["600009.SH"])]))

    def plan(**execution):
        found = delisting_plan(fake, Execution(**execution))
        return list(zip(found["treatment"], found["recovery"]))

    down, half, cash, hold = ("write_down", 0.0), ("write_down", 0.5), ("exit", 1.0), ("hold", None)
    assert plan() == [down, cash, hold, down, down]
    assert plan(delisting="write_down", failure_recovery=0.5) == [half, half, hold, half, down]
    assert plan(delisting="sell_at_last_close") == [cash, cash, hold, cash, down]
    assert plan(delisting="hold") == [hold] * 5


@pytest.mark.parametrize(
    "fields",
    [{"delisting": "never"}, {"failure_recovery": 1.5}, {"delisting": "sell_at_last_close", "failure_recovery": 0.5}],
)
def test_execution_refuses_an_unknown_rule_a_recovery_outside_0_1_or_one_no_rule_reads(fields):
    """Design 2.2 and the one-key-per-behaviour principle: a recovery only means something for auto and write_down."""
    with pytest.raises(ValueError):
        Execution(**fields)
