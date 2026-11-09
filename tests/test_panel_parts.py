import numpy as np
import polars as pl
import pytest

from tests.test_panel import DAYS, A, seed
from wequant.panel import build
from wequant.panel.cache import PanelCache
from wequant.panel.grid import asof_rows, cells, fill_forward, scatter
from wequant.panel.status import MISSING, NO_ROW, NOT_LISTED, SUSPENDED, TRADED, classify

DATES = np.array(DAYS, dtype="datetime64[D]")
CODES = np.array(["000001.SZ", "600000.SH"])


def test_fill_forward_carries_the_last_value_down():
    np.testing.assert_array_equal(fill_forward(np.array([np.nan, 2, np.nan, np.nan, 3]), initial=1), [1, 2, 2, 2, 3])
    grid = np.array([[np.nan, 5.0], [2.0, np.nan], [np.nan, np.nan]])
    np.testing.assert_array_equal(fill_forward(grid, initial=1), [[1, 5], [2, 5], [2, 5]])


def test_asof_rows_moves_a_non_trading_day_to_the_next_row():
    days = np.array(["2024-01-06", "2023-12-29", "2024-01-12", "2024-01-08"], dtype="datetime64[D]")
    assert asof_rows(DATES, days).tolist() == [4, 0, 8, 4]


def test_the_later_of_two_events_on_one_row_wins(tmp_path):
    events = [
        {"code": A, "ex_date": "2024-01-07", "factor": 3.0},
        {"code": A, "ex_date": "2024-01-06", "factor": 2.0},
        {"code": A, "ex_date": "2024-01-02", "factor": 1.0},
        {"code": A, "ex_date": "2024-02-01", "factor": 9.0},
    ]
    store = seed(tmp_path / "home", adj_factor=events)
    src = PanelCache(store, store.manifest(), tmp_path / "cache")
    factor = build.factor(src)[:, list(src.array("codes")).index(A)]
    np.testing.assert_array_equal(factor, [1, 1, 1, 1, 3, 3, 3, 3])
    assert (build.factor(src)[:, [c != A for c in src.array("codes")]] == 1.0).all()


def test_cells_maps_rows_and_columns_and_rejects_strangers():
    frame = pl.DataFrame({"code": ["600000.SH", "000001.SZ"], "date": DATES[[7, 0]]})
    rows, cols = cells(frame, DATES, CODES)
    assert rows.tolist() == [7, 0] and cols.tolist() == [1, 0]
    with pytest.raises(ValueError, match="codes"):
        cells(frame.with_columns(code=pl.lit("600009.SH")), DATES, CODES)
    with pytest.raises(ValueError, match="calendar"):
        cells(frame.with_columns(date=np.array(["2024-01-06", "2024-01-02"], dtype="datetime64[D]")), DATES, CODES)
    with pytest.raises(ValueError, match="calendar"):
        cells(frame.with_columns(date=np.array(["2024-01-12", "2024-01-02"], dtype="datetime64[D]")), DATES, CODES)


def test_scatter_fills_the_rest():
    grid = scatter((2, 3), np.array([0, 1]), np.array([2, 0]), np.array([7, 8], dtype=np.int8), NO_ROW)
    assert grid.dtype == np.int8 and grid.tolist() == [[-1, -1, 7], [8, -1, -1]]


def test_classify_applies_the_first_matching_rule():
    bar = np.array([[1, 1, 0, 0, 0, 0, 0, 0, 0]], dtype=bool)
    tradestatus = np.array([[0, NO_ROW, 0, 0, 1, NO_ROW, NO_ROW, NO_ROW, NO_ROW]], dtype=np.int8)
    dropped = np.array([[1, 0, 1, 0, 0, 1, 1, 0, 0]], dtype=bool)
    span = np.array([[1, 1, 1, 0, 0, 0, 1, 1, 0]], dtype=bool)
    expected = [TRADED, TRADED, SUSPENDED, SUSPENDED, MISSING, MISSING, MISSING, SUSPENDED, NOT_LISTED]
    status = classify(bar, tradestatus, dropped, span)
    assert status.dtype == np.int8 and status.tolist() == [expected]


def test_builders_cover_the_contract_keys():
    keys = "dates codes boards exchanges open high low close preclose volume amount factor status st".split()
    assert list(build.BUILDERS) == keys
