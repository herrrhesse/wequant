"""Ledger frames: schemas, the base row, positions on closed days, named statuses and reasons, run identities."""

import math

import numpy as np
import polars as pl
import pytest

from tests.account.fake import FakePanel
from wequant.account import (
    DAILY,
    EXECUTION_VERSION,
    FILLED,
    ORDER_REASONS,
    ORDERS,
    PARTIAL,
    POSITIONS,
    REJECTED,
    Account,
    Execution,
)
from wequant.analytics import compute_metrics
from wequant.analytics.inputs import ORDER_STATUSES
from wequant.version import code_version

DATES = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
CODES = ["600000.SH", "000001.SZ"]
PRICES = [[10.0, 8.0], [10.0, 8.0], [10.5, math.nan], [10.2, 8.2]]
AMOUNT = [[1e6, 2e6], [5e6, 0.0], [4e6, math.nan], [3e6, math.nan]]  # yuan; NaN on a traded day is a null amount
EXECUTION = Execution(slippage_bps=5)


def run(batches: dict) -> tuple[FakePanel, Account]:
    """A closed run over DATES from the first, with batches[t] traded on row t."""
    panel = FakePanel(DATES, CODES, ["main", "main"], PRICES, PRICES, amount=AMOUNT)
    account = Account(panel, 100_000, DATES[0], EXECUTION)
    for t in range(1, len(DATES)):
        account.begin(t)
        for cols, qty in batches.get(t, []):
            account.trade(cols, qty)
        account.end()
    return panel, account


@pytest.fixture(scope="module")
def traded():
    return run({1: [([0, 1], [1000, 500])], 2: [([0, 1, 1], [-1000, -100, 50])], 3: [([1], [-math.inf])]})


def test_frames_have_the_analytics_schemas(traded):
    ledger = traded[1].ledger()
    assert dict(ledger.daily.schema) == DAILY and list(ledger.daily.columns) == list(DAILY)
    assert dict(ledger.positions.schema) == POSITIONS and list(ledger.positions.columns) == list(POSITIONS)
    assert dict(ledger.orders.schema) == ORDERS and list(ledger.orders.columns) == list(ORDERS)


def test_the_base_row_holds_only_the_initial_cash(traded):
    base = traded[1].ledger().daily.row(0, named=True)
    assert (base["date"].isoformat(), base["equity"], base["cash"], base["bought"], base["sold"]) == (
        DATES[0],
        100_000,
        100_000,
        0,
        0,
    )


def test_positions_are_held_codes_on_closed_days(traded):
    positions = traded[1].ledger().positions
    assert positions["date"].cast(pl.String).unique().sort().to_list() == DATES[1:3]  # all sold on the last day
    assert (positions["shares"] > 0).all()
    assert positions.filter(pl.col("code") == "600000.SH")["date"].cast(pl.String).to_list() == DATES[1:2]


def test_statuses_and_reasons_are_named():
    assert (ORDER_STATUSES[FILLED], ORDER_STATUSES[PARTIAL], ORDER_STATUSES[REJECTED]) == (
        "filled",
        "partial",
        "rejected",
    )
    _, account = run({1: [([0, 1], [1000, 50])], 2: [([0, 0], [500, -math.inf])], 3: [([1], [-100])]})
    orders = account.ledger().orders
    assert orders["status"].to_list() == ["filled", "rejected", "filled", "filled", "rejected"]
    assert orders["reason"].to_list() == [None, "lot", None, None, "no_position"]
    assert orders["side"].to_list() == ["buy", "buy", "buy", "sell", "sell"]
    assert orders["requested"].to_list() == [1000, 50, 500, math.inf, 100]
    assert orders["status"].is_in(ORDER_STATUSES).all() and orders["reason"].drop_nulls().is_in(ORDER_REASONS).all()


def test_prices_without_a_bar_are_null(traded):
    orders = traded[1].ledger().orders
    assert orders["price"].to_list() == [10.0, 8.0, 10.5, None, None, 8.2]
    assert orders["reason"].to_list() == [None, None, None, "no_bar", "no_bar", None]


def test_orders_carry_the_stocks_day_amount_as_the_panel_has_it(traded):
    orders = traded[1].ledger().orders
    assert orders["day_amount"].to_list() == [5e6, 0.0, 4e6, None, None, None]


def test_orders_keep_what_was_sent_although_the_caller_reuses_its_buffers():
    panel = FakePanel(DATES, CODES, ["main", "main"], PRICES, PRICES)
    account = Account(panel, 100_000, DATES[0], EXECUTION)
    cols, qty = np.array([0]), np.array([1000.0])
    account.begin(1)
    got = account.trade(cols, qty)
    for array in got:
        with pytest.raises(ValueError):
            array[0] = 0
    account.end()
    cols[0], qty[0] = 1, -500.0
    account.begin(2)
    account.trade(cols, qty)
    account.end()
    orders = account.ledger().orders.select("code", "side", "requested", "shares", "status")
    assert orders.rows() == [("600000.SH", "buy", 1000, 1000, "filled"), ("000001.SZ", "sell", 500, 0, "rejected")]


def test_columns_of_any_integer_type_make_one_orders_frame():
    _, account = run({1: [(np.array([0], dtype=np.uint64), [1000])], 2: [(np.array([1], dtype=np.int64), [500])]})
    assert account.ledger().orders["code"].to_list() == CODES


def test_meta_holds_the_run_identities(traded):
    panel, account = traded
    assert account.ledger().meta == {
        "store_version": 1,
        "data_key": panel.key,
        "rules_key": "fake-r1",
        "code_version": code_version(),
        "execution": EXECUTION.to_dict(),
        "execution_version": EXECUTION_VERSION,
        "cash": 100_000.0,
        "start": DATES[0],
    }


def test_compute_metrics_takes_the_frames(traded):
    panel, account = traded
    ledger, calendar = account.ledger(), pl.Series("date", panel.dates)
    with_orders = compute_metrics(ledger.daily, calendar, positions=ledger.positions, orders=ledger.orders)
    assert with_orders["executable_order_share"].value == pytest.approx(4 / 6)
    # fills of 10,000, 4,000, 10,500 and 4,100 yuan; the amount is 0 on the 4,000 one's day and NaN on the 4,100 one's
    assert with_orders["capacity_max"].value == pytest.approx(10_500 / 4e6, rel=1e-12)
    assert with_orders["capacity_p95"].value == pytest.approx(0.002 + 0.95 * (0.002625 - 0.002), rel=1e-12)
    assert with_orders["capacity_unknown_fills"].value == 2
    without = compute_metrics(ledger.daily, calendar, positions=ledger.positions)
    assert without["executable_order_share"].reason == "no_orders" and without["turnover"].value > 0


def test_a_run_without_orders_has_an_empty_orders_frame():
    panel, account = run({})
    ledger = account.ledger()
    assert ledger.orders.height == 0 and dict(ledger.orders.schema) == ORDERS
    assert ledger.positions.height == 0 and dict(ledger.positions.schema) == POSITIONS
    metrics = compute_metrics(ledger.daily, pl.Series("date", panel.dates), orders=ledger.orders)
    assert metrics["executable_order_share"].value is None
    assert metrics["executable_order_share"].reason == "no_rebalance_orders"
