"""A null bars amount (a resolution voided it) is no value downstream: NaN in the panel, skipped by operators and
expressions, never 0."""

import numpy as np
import pytest

from tests.frames import table_frame
from wequant import ops
from wequant.data.store import Store
from wequant.expr import GT, MA, SUM, Field, evaluate
from wequant.panel import load_panel

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
AMOUNTS = [1000.0, 2000.0, None, 4000.0]
NAN = np.nan


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    root = tmp_path_factory.mktemp("null_amount")
    rows = [
        {"code": "600000.SH", "date": day, "open": 10000, "high": 10000, "low": 10000, "close": 10000}
        | {"volume": 100, "amount": amount}
        for day, amount in zip(DAYS, AMOUNTS, strict=True)
    ]
    security = {"code": "600000.SH", "exchange": "SH", "board": "main", "first_date": DAYS[0], "last_date": DAYS[-1]}
    store = Store(root / "home")
    store.commit(
        {"source": "test"},
        replace={
            "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
            "securities": table_frame("securities", [security], source="test"),
            "bars": table_frame("bars", rows, preclose_derived=True, source="test"),
        },
    )
    return load_panel(store=store, cache_dir=root / "cache")


def column(grid: np.ndarray) -> list[float]:
    return grid[:, 0].tolist()


def same(a: list[float], b: list[float]) -> bool:
    return np.array_equal(np.array(a), np.array(b), equal_nan=True)


def test_the_panel_shows_a_null_amount_as_nan_on_a_traded_day(panel):
    assert same(column(panel.raw("amount")), [1000.0, 2000.0, NAN, 4000.0])
    assert column(panel.traded) == [True] * 4 and column(panel.raw("volume")) == [100.0] * 4


def test_operators_skip_it_as_no_value(panel):
    amount = panel.raw("amount")
    assert same(column(ops.MA(amount, 2)), [NAN, 1500.0, NAN, 3000.0])  # 3000: the bars of day 2 and day 4
    assert same(column(ops.SUM(amount, 0)), [1000.0, 3000.0, NAN, 7000.0])
    assert same(column(ops.REF(amount, 1)), [NAN, 1000.0, NAN, 2000.0])
    assert same(column(ops.GT(amount, 0)), [1.0, 1.0, NAN, 1.0])  # neither true nor false


def test_expressions_skip_it_as_no_value(panel):
    assert same(column(evaluate(MA(Field("amount"), 2), panel)), [NAN, 1500.0, NAN, 3000.0])
    assert same(column(evaluate(SUM(Field("amount"), 0), panel)), [1000.0, 3000.0, NAN, 7000.0])
    assert same(column(evaluate(GT(Field("amount"), 0), panel)), [1.0, 1.0, NAN, 1.0])
