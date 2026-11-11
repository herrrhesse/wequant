"""Ledger frames: the day amount orders carry, and the ledger's own copies of what each batch sent."""

import math

import numpy as np
import pytest

from tests.account.fake import FakePanel
from wequant.account import (
    Account,
    Execution,
)

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


def test_orders_carry_the_stocks_day_amount_as_the_panel_has_it(traded):
    """day_amount is the panel's amount on the order's day, the capacity metrics' input: NaN reads as null, 0.0 stays."""
    orders = traded[1].ledger().orders
    assert orders["day_amount"].to_list() == [5e6, 0.0, 4e6, None, None, None]


def test_orders_keep_what_was_sent_although_the_caller_reuses_its_buffers():
    """5c16919: the ledger keeps its own int64 and float64 copies of each batch, and Fills are read-only."""
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
    _, mixed = run({1: [(np.array([0], dtype=np.uint64), [1000])], 2: [(np.array([1], dtype=np.int64), [500])]})
    assert mixed.ledger().orders["code"].to_list() == CODES  # columns of any integer type make one frame
