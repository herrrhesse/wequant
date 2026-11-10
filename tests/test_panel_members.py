"""Index membership and listing age on the panel: point-in-time spans, the stored span, the alias and list_days."""

import datetime as dt

import numpy as np
import pytest

from tests.test_panel import DAYS, A, B, C, D, col, seed
from wequant.expr import Field, evaluate
from wequant.panel import load_panel

NAN = np.nan
MEMBERS = [
    {"index_code": "399300.SZ", "code": A, "start_date": DAYS[1], "end_date": DAYS[3]},
    {"index_code": "399300.SZ", "code": D, "start_date": DAYS[3], "end_date": DAYS[6]},
    {"index_code": "399300.SZ", "code": "600002.SH", "start_date": DAYS[2], "end_date": DAYS[2]},  # not on the grid
    {"index_code": "000905.SH", "code": B, "start_date": DAYS[2], "end_date": DAYS[5]},
]


@pytest.fixture
def load(tmp_path):
    store = seed(tmp_path / "home", index_members=MEMBERS)
    return lambda start=None: load_panel(start, store=store, cache_dir=tmp_path / "cache")


@pytest.fixture
def panel(load):
    return load()


def test_membership_holds_from_start_to_end_both_included_and_is_unknown_outside_the_stored_span(panel, load):
    member = panel.member("399300.SZ")
    assert member.dtype == np.int8
    assert member[:, col(panel, A)].tolist() == [-1, 1, 1, 1, 0, 0, 0, -1]
    assert member[:, col(panel, D)].tolist() == [-1, 0, 0, 1, 1, 1, 1, -1]
    assert member[:, col(panel, B)].tolist() == [-1, 0, 0, 0, 0, 0, 0, -1]
    assert panel.index_span("399300.SZ") == (dt.date(2024, 1, 3), dt.date(2024, 1, 10))
    assert (panel.member("000852.SH") == -1).all() and panel.index_span("000852.SH") is None
    later = load(DAYS[2])
    assert np.array_equal(later.member("399300.SZ"), panel.member("399300.SZ")[2:])


def test_member_reads_the_stored_code_of_an_alias_and_is_empty_off_bars_and_outside_the_span(panel):
    csi300 = evaluate(Field("member@000300.SH"), panel)
    # A trades on days 1, 2, 5, 6, 7 and 8; D on days 1, 2, 4 and 6
    assert np.array_equal(csi300[:, col(panel, A)], [NAN, 1, NAN, NAN, 0, 0, 0, NAN], equal_nan=True)
    assert np.array_equal(csi300[:, col(panel, D)], [NAN, 0, NAN, 1, NAN, 1, NAN, NAN], equal_nan=True)
    csi500 = evaluate(Field("member@000905.SH"), panel)
    assert np.array_equal(csi500[:, col(panel, B)], [NAN, NAN, 1, 1, NAN, 1, NAN, NAN], equal_nan=True)
    assert np.isnan(evaluate(Field("member@000852.SH"), panel)).all()


def test_list_days_count_trading_days_from_first_date_whatever_the_panel_starts_on(panel, load):
    assert panel.list_days.dtype == np.int16
    assert panel.list_days[:, col(panel, A)].tolist() == [1, 2, 3, 4, 5, 6, 7, 8]
    assert panel.list_days[:, col(panel, B)].tolist() == [0, 0, 1, 2, 3, 4, 5, 6]
    later = load(DAYS[3])
    assert np.array_equal(later.list_days, panel.list_days[3:])
    field = evaluate(Field("list_days"), later)
    assert np.array_equal(field[:, list(later.codes).index(B)], [2, NAN, 4, NAN, NAN], equal_nan=True)
    field = evaluate(Field("list_days"), panel)  # empty on a day without a bar
    assert np.array_equal(field[:, col(panel, A)], [1, 2, NAN, NAN, 5, 6, 7, 8], equal_nan=True)
    assert field[:, col(panel, C)].tolist() == [1, 2, 3, 4, 5, 6, 7, 8]
