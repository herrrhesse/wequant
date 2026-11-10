"""Design 15.4 and v4 D11: a panel reads the days of the excluded levels' anomalies as missing data, never a notice and
never an adjustment; each test names the rule it guards."""

import math

import numpy as np
import polars as pl
import pytest

from tests.backtest.market import WEEK, A, B, build_store
from tests.frames import table_frame
from wequant.panel import MISSING, TRADED, load_panel

ISSUES = [  # (code, day, table, kind, action)
    (A, 2, "bars", "price_mismatch", "resolved_baostock"),  # error, price
    (A, 3, "daily_status", "tradestatus_conflict", "bar_kept"),  # suspicious, status
    (B, 2, "bars", "bar_added", "row_added"),  # notice, bar
    (B, 3, "adj_factor", "adj_blip", "excluded_from_adj_factor_and_preclose"),  # suspicious, adjustment
]


def store_with(home, issues):
    store = build_store(home, WEEK, {A: [(10.0, 10.0)] * 5, B: [(20.0, 20.0)] * 5})
    rows = [{"table": t, "code": c, "date": WEEK[d], "kind": k, "action": a} for c, d, t, k, a in issues]
    store.commit({}, replace={"data_issues": table_frame("data_issues", rows, source="test")})
    return store


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return store_with(tmp_path_factory.mktemp("anomalies") / "home", ISSUES)


def missing(panel):
    """The (row, column) cells the panel reads as missing although they have a bar."""
    return sorted(zip(*np.nonzero(panel.status == MISSING)))


def test_flag_reads_every_value_and_marks_every_anomalous_cell(store, tmp_path):
    """D11 flag, the default: no value changes; each anomalous cell is listed with its class and level."""
    panel = load_panel(store=store, cache_dir=tmp_path)
    assert (panel.status == TRADED).all() and not panel.anomalies["excluded"].any()
    assert panel.anomalies.select("row", "code", "class", "level").rows() == [
        (2, B, "bar", "notice"),
        (2, A, "price", "error"),
        (3, B, "adjustment", "suspicious"),
        (3, A, "status", "suspicious"),
    ]


def test_excluding_errors_reads_their_days_as_missing_in_every_bar_field_but_the_factor(store, tmp_path):
    """D11: exclusion acts on error-level anomalies; the whole stock-day reads as missing, the factor stays."""
    flagged, panel = (load_panel(store=store, cache_dir=tmp_path, exclude=levels) for levels in ((), ("error",)))
    a = list(panel.codes).index(A)
    assert missing(panel) == [(2, a)] and panel.key != flagged.key
    assert math.isnan(panel.raw("close")[2, a]) and math.isnan(panel.limit_up_price[2, a]) and not panel.traded[2, a]
    assert panel.factor[2, a] == 1.0 and panel.raw("close")[1, a] == 10.0


def test_the_suspicious_switch_adds_their_days_but_never_an_adjustment_or_a_notice(store, tmp_path):
    """D11: a separate switch excludes suspicious anomalies too; a missing day cannot fix an adjustment ratio, and
    notices are never excluded."""
    panel = load_panel(store=store, cache_dir=tmp_path, exclude=("error", "suspicious"))
    a = list(panel.codes).index(A)
    assert missing(panel) == [(2, a), (3, a)]
    assert panel.anomalies.filter(pl.col("excluded"))["class"].to_list() == ["price", "status"]


def test_an_anomaly_row_of_a_kind_issue_kinds_lacks_stops_the_panel(tmp_path):
    """A-1 and no silent fixes: an unclassified kind is an error, not an unflagged cell."""
    store = store_with(tmp_path / "home", [(A, 2, "bars", "new_kind", "flagged")])
    with pytest.raises(ValueError, match="does not classify: \\['new_kind'\\]"):
        load_panel(store=store, cache_dir=tmp_path / "cache").anomalies
