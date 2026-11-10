"""D11: which anomalous cells a run used, by signal window, fill and valuation; G-D11 (design 15.6) through the engine.
Each test names the rule it guards; the panel's exclusion itself is tested on the panel."""

import polars as pl
import pytest

from tests.backtest.market import build_store, business_days
from tests.frames import table_frame
from wequant.account import Execution
from wequant.backtest import Backtest, DataOptions, run
from wequant.backtest.anomalies import signal_uses
from wequant.expr import EMA, REF, Field
from wequant.panel import load_panel
from wequant.strategy import Factor, Strategy

DAYS = business_days(30)
A, B = "600000.SH", "000001.SZ"
ADJ, CLOSE = Field("adj_close"), Field("close")
EX0 = Execution(slippage_bps=0.0)
RET = Strategy(factors=(Factor("ret", ADJ / REF(ADJ, 1) - 1),), top_n=1, period="day")
HOLD_A = Strategy(factors=(Factor("level", CLOSE, prefer="low"),), top_n=1, period="day", every=1000)
# A closes 10.00 but 11.00 on day 10 (+10%, the day's best return) and opens day 11 at 10.50; B climbs 0.02 a day
BARS = {
    A: [(10.0, 10.0)] * 10 + [(10.0, 11.0), (10.5, 10.0)] + [(10.0, 10.0)] * 18,
    B: [(round(20 + 0.02 * max(t - 1, 0), 2), round(20 + 0.02 * t, 2)) for t in range(30)],
}
PRICE = ("bars", "price_mismatch", "resolved_baostock")  # error, price
BLIP = ("adj_factor", "adj_blip", "excluded_from_adj_factor_and_preclose")  # suspicious, adjustment


def panel_of(home, issues=(), exclude=()):
    """The market with each (table, kind, action) of issues on A's day 10."""
    store = build_store(home / "home", DAYS, BARS)
    rows = [{"table": t, "code": A, "date": DAYS[10], "kind": k, "action": a} for t, k, a in issues]
    store.commit({}, replace={"data_issues": table_frame("data_issues", rows, source="test")})
    return load_panel(store=store, cache_dir=home / "cache", exclude=exclude)


def run_on(panel, strategy=RET, data=DataOptions()):
    return run(Backtest(strategy, cash=100_000, execution=EX0, benchmark=None, data=data), panel)


def trades(result):
    return result.ledger.orders.select("date", "code", "side").rows()


def day(k):
    return pl.Series([DAYS[k]]).str.to_date().item()


def test_a_window_holds_lookback_plus_one_bars_and_an_adjustment_counts_only_when_spanned(tmp_path):
    """Design 15.2: REF(x, 1) reads its decision's bar and the one before; EMA reads every earlier bar; an adjustment
    is read by a window with bars on both sides of it; a gap is never read in a window."""
    issues = (PRICE, BLIP, ("bars", "off_scale", "row_dropped"))
    panel = panel_of(tmp_path, issues)
    strategy = Strategy(factors=(Factor("ret", ADJ / REF(ADJ, 1)), Factor("trend", EMA(ADJ, 3))), top_n=1)
    decisions = pl.DataFrame({"row": [10, 11, 12], "col": [list(panel.codes).index(A)] * 3, "decision": ["keep"] * 3})
    uses = signal_uses(strategy, panel, decisions, panel.anomalies)
    got = uses.sort("row", "class").select("row", "class", "root", "full_history").rows()
    assert got == [
        (10, "adjustment", "ret", False),  # bars on days 9 and 10
        (10, "price", "ret", False),
        (11, "adjustment", "trend", True),  # ret's window starts on day 10 itself
        (11, "price", "ret", False),  # bars on days 10 and 11
        (12, "adjustment", "trend", True),
        (12, "price", "trend", True),
    ]


@pytest.fixture(scope="module")
def flagged(tmp_path_factory):
    return run_on(panel_of(tmp_path_factory.mktemp("flag"), [PRICE]))


def test_a_run_that_reads_an_error_cell_records_each_decision_and_warns_by_level(flagged):
    """D11 and G-D11 flag: A's anomalous day-10 close makes it the pick and then a sell; both decisions read the cell,
    the warning counts it by level, and no order or holding sits on it."""
    assert trades(flagged)[1:] == [(day(11), B, "sell"), (day(11), A, "buy"), (day(12), A, "sell"), (day(12), B, "buy")]
    columns = ("date", "code", "channel", "anomaly_date", "kind", "level", "root", "decision")
    assert flagged.anomalies.select(columns).rows() == [
        (day(10), A, "signal", day(10), "price_mismatch", "error", "ret", "buy"),
        (day(11), A, "signal", day(10), "price_mismatch", "error", "ret", "sell"),
    ]
    params = flagged.warnings["data_anomaly"][1]
    assert params == {
        "codes": 1,
        "cells": 1,
        "error_cells": 1,
        "suspicious_cells": 0,
        "notice_cells": 0,
        "decisions": 2,
        "fills": 0,
        "max_share": None,
        "max_share_day": None,
        "unavoidable": None,
    }


def test_excluding_the_error_day_removes_its_use_and_the_warning_and_changes_the_run_id(flagged, tmp_path):
    """D11 and G-D11 exclude_day: A's day 10 reads as missing, so A never ranks first (REF skips to day 9's close)."""
    data = DataOptions(anomalies="exclude_day")
    result = run_on(panel_of(tmp_path, [PRICE], exclude=data.excluded), data=data)
    assert {code for _, code, _ in trades(result)} == {B} and result.anomalies.is_empty()
    assert "data_anomaly" not in result.warnings and result.id != flagged.id


def test_without_anomalies_both_options_give_the_same_run_under_other_ids(tmp_path):
    """G-D11 control: the option changes no result where nothing is anomalous, only the run's identity."""
    data = DataOptions(anomalies="exclude_day")
    flag, excluded = (
        run_on(panel_of(tmp_path / "f")),
        run_on(panel_of(tmp_path / "x", exclude=data.excluded), data=data),
    )
    assert flag.ledger.daily.equals(excluded.ledger.daily) and flag.ledger.orders.equals(excluded.ledger.orders)
    assert flag.picks.equals(excluded.picks) and flag.id != excluded.id


def test_a_holding_valued_on_the_anomalous_close_is_a_use_that_exclusion_values_at_the_day_before(tmp_path):
    """G-D11 valuation: held at day 10's close, A is valued at the anomalous 11.00; excluded, at day 9's 10.00."""
    flag = run_on(panel_of(tmp_path / "f", [PRICE]), HOLD_A)
    shares, cash = 9_900.0, 100_000 - 99_000 - 29.70 - 0.99  # day 1's buy at 10.00 with commission and transfer fee
    assert flag.ledger.positions["shares"][0] == shares
    used = flag.anomalies.select("date", "channel", "value", "share").rows()
    assert used == [(day(10), "valuation", 108_900.0, pytest.approx(108_900 / (cash + 108_900)))]
    data = DataOptions(anomalies="exclude_day")
    excluded = run_on(panel_of(tmp_path / "x", [PRICE], exclude=data.excluded), HOLD_A, data)
    assert excluded.anomalies.is_empty()
    assert excluded.ledger.daily.filter(pl.col("date") == day(10))["equity"].item() == pytest.approx(cash + 99_000)


def test_a_holding_across_an_adjustment_day_is_a_valuation_use_at_the_close_before(tmp_path):
    """Design 15.1: an adjustment taints a holding rescaled at the day's open; it was last valued the close before."""
    result = run_on(panel_of(tmp_path, [BLIP]), HOLD_A)
    cash = 100_000 - 99_000 - 29.70 - 0.99
    used = result.anomalies.select("date", "channel", "value", "share").rows()
    assert used == [(day(10), "valuation", 99_000.0, pytest.approx(99_000 / (cash + 99_000)))]  # 9,900 x 10.00


def test_an_excluded_adjustment_stays_used_and_counts_as_unavoidable(tmp_path):
    """Design 15.4 and G-D11: no missing day fixes an adjustment ratio, so its use stays and the warning says so."""
    data = DataOptions(anomalies="exclude_day", exclude_suspicious=True)
    result = run_on(panel_of(tmp_path, [BLIP], exclude=data.excluded), data=data)
    assert result.anomalies.select("date", "class", "decision").rows() == [(day(10), "adjustment", "buy")]
    assert result.warnings["data_anomaly"][1]["unavoidable"] == 1


@pytest.mark.parametrize("fields", [{"anomalies": "exclude_window"}, {"exclude_suspicious": True}])
def test_data_options_refuse_an_unknown_mode_and_a_switch_flag_would_ignore(fields):
    """Design 15.4 (flag or exclude_day only) and one key per behaviour: the switch only means something when excluding."""
    with pytest.raises(ValueError):
        DataOptions(**fields)
