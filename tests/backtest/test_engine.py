"""The rebalance engine on small stored markets: hand-computed ledgers, decision frames, progress, cancel, no look-ahead."""

import datetime as dt
import math
from dataclasses import replace

import polars as pl
import pytest

from tests.backtest.market import (
    CSI300,
    MIXED_BOARDS,
    ORDER_COLUMNS,
    ROTATION,
    WEEK,
    A,
    B,
    build_store,
    business_days,
    mixed_market,
)
from wequant.account import EXECUTION_VERSION, Execution
from wequant.analytics import METRICS_VERSION, compute_metrics
from wequant.backtest import Backtest, Cancelled, planned_id, run, versions
from wequant.backtest.warnings import WARNINGS
from wequant.expr import EXPR_VERSION, REF, Cache, Field, panel_key
from wequant.panel import load_panel
from wequant.strategy import Condition, Factor, SpecError, Strategy

INF = math.inf
EX0 = Execution(slippage_bps=0.0)
ADJ, CLOSE = Field("adj_close"), Field("close")
RET1 = Strategy(factors=(Factor("ret1", ADJ / REF(ADJ, 1)),), top_n=1, period="day")
CHEAP = Factor("cheap", CLOSE, prefer="low")
ONE = Strategy(factors=(Factor("one", CLOSE),), top_n=1, period="day", every=1000)
LIMITS = "the store's price limits are not checked against its bars; run tools.check_limits --commit"
JAN = {n: dt.date(2024, 1, n) for n in range(1, 32)}


def panel_of(home, days, bars, end=None, **market):
    store = build_store(home / "home", days, bars, **market)
    return load_panel(end=end, store=store, cache_dir=home / "cache")


def orders(result):
    return result.ledger.orders.select(ORDER_COLUMNS).rows()


def column(result, name, frame="daily"):
    return getattr(result.ledger, frame)[name].to_list()


@pytest.fixture(scope="module")
def rotation(tmp_path_factory):
    return panel_of(tmp_path_factory.mktemp("rotation"), WEEK, ROTATION, index=CSI300)


@pytest.fixture(scope="module")
def rotated(rotation):
    calls = []
    result = run(Backtest(RET1, cash=100_000, execution=EX0), rotation, progress=calls.append)
    return result, calls


def test_rotation_orders_and_daily_golden(rotated):
    result, _ = rotated
    assert orders(result) == [
        (JAN[4], A, "buy", 9615.0, 9600.0, 10.4, 99840.0, 29.95, 0.0, 1.0, 0.0, "filled", None),
        (JAN[5], A, "sell", INF, 9600.0, 10.6, 101760.0, 30.53, 50.88, 1.02, 0.0, "filled", None),
        (JAN[5], B, "buy", 4624.0, 4600.0, 21.2, 97520.0, 29.26, 0.0, 0.98, 0.0, "filled", None),
        (JAN[8], B, "sell", INF, 4600.0, 21.4, 98440.0, 29.53, 49.22, 0.98, 0.0, "filled", None),
        (JAN[8], A, "buy", 9414.0, 9400.0, 10.9, 102460.0, 30.74, 0.0, 1.02, 0.0, "filled", None),
    ]
    assert column(result, "equity") == pytest.approx([100_000.0, 100_000.0, 98_049.05, 103_156.38, 103_524.89])
    assert column(result, "cash") == [100_000.0, 100_000.0, 129.05, 4_256.38, 124.89]
    assert column(result, "bought") == [0.0, 0.0, 99_840.0, 97_520.0, 102_460.0]
    assert column(result, "sold") == [0.0, 0.0, 0.0, 101_760.0, 98_440.0]
    assert column(result, "benchmark") == [3000.0, 3030.0, 2970.0, 3060.0, 3090.0]
    assert result.metrics["total_return"].value == pytest.approx(0.03524890000000003, abs=1e-15)


def test_rotation_decision_frames(rotated):
    result, _ = rotated
    assert result.picks.rows() == [
        (JAN[3], A, "target", 1, 1.0, 1.0, 1.0, False),
        (JAN[4], B, "target", 1, 1.0, 1.0, 1.0, False),
        (JAN[5], A, "target", 1, 1.0, 1.0, 1.0, False),
    ]
    listed = [("listed", 2, 2), ("bar", 2, 2), ("universe", 2, 2)]
    first = [*listed, ("scored", 0, 0), ("targets", 0, 0), ("backups", 0, 0)]
    later = [*listed, ("scored", 2, 2), ("targets", 1, 1), ("backups", 0, 0)]
    funnel = result.funnel.select("date", "step", "passed", "left").rows()
    assert funnel == [
        (day, *row) for day, rows in ((JAN[2], first), *((JAN[d], later) for d in (3, 4, 5))) for row in rows
    ]
    assert result.detail.columns == ["date", "code", "universe", "scored", "rank", "held", "action"] + [
        "sell_kind",
        "sell_step",
        "sell_rank",
        "sell_limit",
    ]
    none, rank_sell = ("none", None, None, None, None), ("sell", "rank", None, 2, 1)
    assert result.detail.rows() == [
        (JAN[2], B, True, False, None, False, *none),
        (JAN[2], A, True, False, None, False, *none),
        (JAN[3], B, True, True, 2, False, *none),
        (JAN[3], A, True, True, 1, False, "buy", None, None, None, None),
        (JAN[4], B, True, True, 1, False, "buy", None, None, None, None),
        (JAN[4], A, True, True, 2, True, *rank_sell),  # held at the close of JAN[4], ranked out of top_n 1
        (JAN[5], B, True, True, 2, True, *rank_sell),
        (JAN[5], A, True, True, 1, False, "buy", None, None, None, None),
    ]


def test_rotation_identities_progress_and_warnings(rotated, rotation):
    result, calls = rotated
    backtest = Backtest(RET1, cash=100_000, execution=EX0)
    assert result.warnings == {"limits_unchecked": LIMITS}
    assert [call[:3] for call in calls] == [(0, 4, JAN[2]), (4, 4, JAN[8])]
    assert [call.equity for call in calls] == pytest.approx([100_000.0, 103_524.89])
    assert planned_id(backtest, rotation) == result.id
    assert result.engine == "rebalance" and result.description == backtest.form
    assert result.versions == versions("rebalance", 1)
    assert result.versions == {
        "rebalance": 1,
        "strategy": 2,
        "expr": EXPR_VERSION,
        "execution": EXECUTION_VERSION,
        "metrics": METRICS_VERSION,
    }
    assert result.panel_key == panel_key(rotation) and result.anchor == JAN[2]
    ledger = result.ledger
    calendar = pl.Series("date", rotation.dates)
    want = compute_metrics(ledger.daily, calendar, result.spec, positions=ledger.positions, orders=ledger.orders)
    assert result.metrics == want and result.spec == backtest.metrics


def test_rotation_with_10_bps_of_slippage(rotation):
    result = run(Backtest(RET1, cash=100_000, execution=Execution(slippage_bps=10.0)), rotation)
    got = result.ledger.orders.select("date", "code", "requested", "shares", "value", "commission", "slippage").rows()
    assert got == [
        (JAN[4], A, 9615.0, 9600.0, 99840.0, 29.95, 99.84),
        (JAN[5], A, INF, 9600.0, 101760.0, 30.53, 101.76),
        (JAN[5], B, 4620.0, 4600.0, 97520.0, 29.26, 97.52),
        (JAN[8], B, INF, 4600.0, 98440.0, 29.53, 98.44),
        (JAN[8], A, 9377.0, 9300.0, 101370.0, 30.41, 101.37),
    ]
    assert column(result, "cash")[2:] == [29.21, 3_957.26, 716.30]
    assert column(result, "equity")[2:] == pytest.approx([97_949.21, 102_857.26, 103_016.30])
    assert result.metrics["total_return"].value == pytest.approx(0.03016299999999994, abs=1e-15)


STAND_IN = {
    A: [(10.00, 10.00, 10.00), (11.00, 11.00, 10.00), (11.00, 11.20, 11.00)],
    B: [(20.00, 20.00, 20.00), (21.00, 21.50, 20.00), (21.50, 21.60, 21.50)],
}
STAND_IN_STRATEGY = Strategy(factors=(CHEAP,), top_n=1, backups=1, period="day")
ST_BAND = "1 buys at the +ST-band price and 0 sells at the -ST-band price where ST is unknown"
FIRST_DAY = [  # A opens at its up limit; B, the backup, stands in for it
    (JAN[3], A, "buy", 9090.0, 0.0, 11.0, 0.0, 0.0, 0.0, 0.0, 0.0, "rejected", "limit_up"),
    (JAN[3], B, "buy", 4761.0, 4700.0, 21.0, 98700.0, 29.61, 0.0, 0.99, 0.0, "filled", None),
]


def stand_in(tmp_path, bars):
    panel = panel_of(tmp_path, WEEK[:3], bars)
    return run(Backtest(STAND_IN_STRATEGY, cash=100_000, execution=EX0, benchmark=None), panel)


def test_a_backup_stands_in_for_a_target_refused_at_its_up_limit(tmp_path):
    result = stand_in(tmp_path, STAND_IN)
    picks = [(A, "target", 1, 1.0, 1.0, 1.0, False), (B, "backup", 2, 0.5, None, None, False)]
    assert result.picks.rows() == [(day, *pick) for day in (JAN[2], JAN[3]) for pick in picks]
    assert orders(result) == FIRST_DAY + [
        (JAN[4], B, "sell", INF, 4700.0, 21.5, 101050.0, 30.32, 50.53, 1.01, 0.0, "filled", None),
        (JAN[4], A, "buy", 9294.0, 9200.0, 11.0, 101200.0, 30.36, 0.0, 1.01, 0.0, "filled", None),
    ]
    assert column(result, "cash")[1:] == [1_269.40, 1_006.17]
    assert column(result, "equity")[1:] == pytest.approx([102_319.40, 104_046.17])
    assert result.warnings == {"limits_unchecked": LIMITS, "st_band_fills": ST_BAND}


def test_a_backup_sold_at_the_open_buys_back_in_for_a_target_refused_again(tmp_path):
    result = stand_in(tmp_path, {**STAND_IN, A: [*STAND_IN[A][:2], (12.10, 12.10, 11.00)]})
    assert orders(result) == FIRST_DAY + [
        (JAN[4], B, "sell", INF, 4700.0, 21.5, 101050.0, 30.32, 50.53, 1.01, 0.0, "filled", None),
        (JAN[4], A, "buy", 8449.0, 0.0, 12.1, 0.0, 0.0, 0.0, 0.0, 0.0, "rejected", "limit_up"),
        (JAN[4], B, "buy", 4755.0, 4700.0, 21.5, 101050.0, 30.32, 0.0, 1.01, 0.0, "filled", None),
    ]
    assert column(result, "cash")[-1] == 1_156.21
    assert column(result, "equity")[-1] == pytest.approx(102_676.21) == 1_156.21 + 4_700 * 21.60
    assert result.warnings == {"limits_unchecked": LIMITS, "st_band_fills": ST_BAND}


def test_buy_and_hold_across_an_ex_date(tmp_path):
    bars = {A: [(10.00, 10.00, 10.00), (10.30, 10.00, 10.00), (8.00, 8.00, 8.00), (8.20, 8.40, 8.00)]}
    factors = [(A, WEEK[0], 1.0), (A, WEEK[2], 1.25)]
    panel = panel_of(tmp_path, WEEK[:4], bars, factors=factors)
    result = run(Backtest(ONE, cash=100_000, execution=EX0, benchmark=None), panel)
    assert orders(result) == [(JAN[3], A, "buy", 9708.0, 9700.0, 10.3, 99910.0, 29.97, 0.0, 1.0, 0.0, "filled", None)]
    assert column(result, "equity") == pytest.approx([100_000.0, 97_059.03, 97_059.03, 101_909.03])
    assert column(result, "cash") == [100_000.0, 59.03, 59.03, 59.03]
    assert result.ledger.positions.select("date", "shares", "value").rows() == [
        (JAN[3], 9700.0, 97_000.0),
        (JAN[4], 12_125.0, 97_000.0),
        (JAN[5], 12_125.0, 101_850.0),
    ]
    assert column(result, "equity")[-1] == pytest.approx(100_000 - 99_910 - 30.97 + 99_910 * (8.40 * 1.25) / 10.30)


def test_no_signal_leaves_the_cash_alone(rotation):
    never = Strategy(filters=(Condition("never", CLOSE < 0.0),))
    result = run(Backtest(never, cash=100_000, execution=EX0), rotation)
    assert result.ledger.orders.is_empty() and result.ledger.positions.is_empty()
    assert column(result, "equity") == [100_000.0] * 5
    assert result.metrics["total_return"].value == 0.0
    share = result.metrics["executable_order_share"]
    assert (share.value, share.reason) == (None, "no_rebalance_orders")
    assert list(result.warnings) == ["limits_unchecked", "no_picks"]
    steps = [("listed", 2, 2), ("bar", 2, 2), ("universe", 2, 2), ("never", 0, 0)]
    steps += [("scored", 0, 0), ("targets", 0, 0), ("backups", 0, 0)]
    funnel = result.funnel.select("date", "step", "passed", "left").rows()
    assert funnel == [(day, *step) for day in (JAN[2], JAN[5]) for step in steps]
    none = (False, "none", None, None, None, None)
    assert result.detail.rows() == [
        (day, code, True, False, False, None, *none) for day in (JAN[2], JAN[5]) for code in (B, A)
    ]


def test_a_holding_without_a_bar_on_a_decision_day_is_left_alone(tmp_path):
    bars = {A: [(10.0, 10.0), (10.3, 10.0), None, (10.0, 10.2), (10.2, 10.4)], B: [(80.0, 80.0)] * 5}
    panel = panel_of(tmp_path, WEEK, bars)
    result = run(
        Backtest(Strategy(factors=(CHEAP,), top_n=1, period="day"), cash=100_000, execution=EX0, benchmark=None), panel
    )
    assert result.picks.select("date", "code").rows() == [(JAN[2], A), (JAN[3], A), (JAN[4], B), (JAN[5], A)]
    assert orders(result) == [(JAN[3], A, "buy", 9708.0, 9700.0, 10.3, 99910.0, 29.97, 0.0, 1.0, 0.0, "filled", None)]
    assert column(result, "equity") == pytest.approx([100_000.0, 97_059.03, 97_059.03, 98_999.03, 100_939.03])
    assert result.ledger.positions.select("date", "code").rows() == [(JAN[d], A) for d in (3, 4, 5, 8)]


def test_an_unknown_benchmark_raises_before_any_day_runs(rotation):
    calls = []
    with pytest.raises(SpecError, match="^benchmark: 000905.SH has no close") as raised:
        run(Backtest(RET1, benchmark="000905.SH", execution=EX0), rotation, progress=calls.append)
    assert (raised.value.code, raised.value.loc) == ("no_data", ("benchmark",))
    assert calls == []


def counting(after: int):
    """A cancel signal that turns True on its call number after + 1, counting its calls."""
    calls = []

    def cancel() -> bool:
        calls.append(None)
        return len(calls) > after

    return cancel, calls


def test_a_cancel_before_select_stops_everything(rotation):
    progress = []
    with pytest.raises(Cancelled):
        run(Backtest(RET1, execution=EX0), rotation, progress=progress.append, cancel=lambda: True)
    assert progress == []


def test_a_cancel_inside_select_stops_after_the_root_it_follows(rotation):
    progress, cache = [], Cache()
    cancel, calls = counting(2)
    with pytest.raises(Cancelled, match="run cancelled"):
        run(Backtest(RET1, execution=EX0), rotation, cache=cache, progress=progress.append, cancel=cancel)
    assert len(calls) == 3 and progress == [] and cache.misses == 1  # before select, then universe and pool, one root


def test_a_cancel_between_checkpoints_stops_the_day_loop(tmp_path):
    days = business_days(45)
    panel = panel_of(tmp_path, days, {A: [(10.0, 10.0)] * 45, B: [(20.0, 20.5)] * 45})
    backtest = Backtest(Strategy(factors=(CHEAP,), top_n=1), execution=EX0, benchmark=None)
    progress = []
    assert run(backtest, panel, progress=progress.append).ledger.daily.height == 45
    assert [(p.done, p.total) for p in progress] == [(0, 44), (20, 44), (40, 44), (44, 44)]
    progress = []
    with pytest.raises(Cancelled):
        run(backtest, panel, progress=progress.append, cancel=lambda: len(progress) >= 2)
    assert [(p.done, p.day) for p in progress] == [(0, JAN[2]), (20, dt.date.fromisoformat(days[20]))]


MOMENTUM = Strategy(factors=(Factor("mom", ADJ / REF(ADJ, 3) - 1),), top_n=2, backups=1, period="day")
K = 12  # the poisoned row


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    home = tmp_path_factory.mktemp("mixed")
    days, bars = mixed_market()
    store = build_store(home / "home", days, bars, boards=MIXED_BOARDS)
    return days, bars, store, load_panel(store=store, cache_dir=home / "cache")


def before(frame: pl.DataFrame, day, inclusive=True) -> pl.DataFrame:
    return frame.filter(pl.col("date") <= day if inclusive else pl.col("date") < day)


def test_a_panel_cut_at_a_row_changes_nothing_before_it(mixed, tmp_path):
    days, _, store, panel = mixed
    backtest = Backtest(MOMENTUM, execution=EX0, benchmark=None)
    full = run(backtest, panel)
    cut = run(backtest, load_panel(end=days[K], store=store, cache_dir=tmp_path / "cache"))
    day = dt.date.fromisoformat(days[K])
    assert cut.ledger.daily.equals(before(full.ledger.daily, day))
    assert cut.ledger.orders.equals(before(full.ledger.orders, day))
    for name in ("picks", "detail", "funnel"):
        assert getattr(cut, name).equals(before(getattr(full, name), day, inclusive=False))
    assert cut.id != full.id


def test_a_later_close_changes_no_order_up_to_its_day(mixed, tmp_path):
    days, bars, _, panel = mixed
    poisoned = {code: [*seq[: K + 1], poison(seq[K + 1]), *seq[K + 2 :]] for code, seq in bars.items()}
    other = panel_of(tmp_path, days, poisoned, boards=MIXED_BOARDS)
    backtest = Backtest(MOMENTUM, execution=EX0, benchmark=None)
    clean, dirty = run(backtest, panel), run(backtest, other)
    day = dt.date.fromisoformat(days[K + 1])
    keep = ["date", "code", "side", "requested", "shares", "price"]
    assert before(clean.ledger.orders, day).select(keep).equals(before(dirty.ledger.orders, day).select(keep))
    assert not before(clean.ledger.orders, day).is_empty()
    equity = [run_.ledger.daily.filter(pl.col("date") == day)["equity"].item() for run_ in (clean, dirty)]
    assert equity[0] != equity[1]


def poison(bar):
    return None if bar is None else (bar[0], round(bar[1] * 1.04, 2), bar[2])


def test_a_universe_admitting_beijing_raises_from_select(mixed):
    panel = mixed[-1]
    with pytest.raises(ValueError, match="^universe: admits 830799.BJ"):
        run(Backtest(replace(MOMENTUM, universe=CLOSE > 0.0), execution=EX0, benchmark=None), panel)


def test_warnings_follow_their_fixed_order(rotated):
    result, _ = rotated
    assert list(result.warnings) == [code for code in WARNINGS if code in result.warnings]
