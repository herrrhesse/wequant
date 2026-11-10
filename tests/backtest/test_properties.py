"""Engine invariants over random strategies, pools and cross-sectional ranks on a six-code market with gaps and a
Beijing code."""

from dataclasses import replace

import numpy as np
import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.backtest.market import MIXED_BOARDS, build_store, mixed_market
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.expr import CS_PCT, CS_ZSCORE, REF, STD, Cache, Field
from wequant.panel import load_panel
from wequant.strategy import (
    MODES,
    PERIODS,
    POOL_BOARDS,
    Condition,
    Factor,
    Group,
    Pool,
    Strategy,
    choose,
    picks_frame,
    screen,
    select,
    stack,
)

ADJ, CLOSE, OPEN = Field("adj_close"), Field("close"), Field("open")
FACTORS = (
    Factor("mom", ADJ / REF(ADJ, 3) - 1),
    Factor("calm", STD(ADJ / REF(ADJ, 1) - 1, 5), prefer="low"),
    Factor("cheap", CLOSE, prefer="low", weight=2),
)
LIQUID = Condition("liquid", Field("amount") > 1e6)
TRADABLE = (Condition("rising", CLOSE > OPEN), Condition("not_up", ~Field("limit_up")))
CROSS_SECTIONAL = {  # ranked within the pool
    "factor": Factor("relative", CS_ZSCORE(ADJ / REF(ADJ, 1)), prefer="low"),
    "condition": Condition("upper", CS_PCT(CLOSE) > 0.3),
}
HOLDINGS = [
    "held",
    "action",
    "sell_kind",
    "sell_step",
    "sell_rank",
    "sell_limit",
]  # what a screen without holdings lacks


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("mixed")
    store = build_store(home / "home", *mixed_market(), boards=MIXED_BOARDS)
    return load_panel(store=store, cache_dir=home / "cache")


@st.composite
def pools(draw) -> Pool:
    boards = draw(st.none() | st.lists(st.sampled_from(POOL_BOARDS), min_size=1, max_size=3, unique=True))
    return Pool(
        boards=None if boards is None else tuple(boards),
        exclude_st=draw(st.booleans()),
        min_list_days=draw(st.none() | st.integers(2, 8)),
    )


@st.composite
def strategies(draw) -> Strategy:
    flag, cross = st.booleans(), draw(st.sampled_from(sorted(CROSS_SECTIONAL)))
    factors = tuple(replace(f, enabled=draw(flag)) for f in FACTORS)
    factors += (CROSS_SECTIONAL["factor"],) if cross == "factor" else ()
    ranked = any(f.enabled for f in factors)
    top_n = draw(st.none() | st.integers(1, 5)) if ranked else None
    weighting = draw(st.sampled_from(["equal", "formula", *(["score"] if ranked else [])]))
    tradable = Group("tradable", tuple(replace(c, enabled=draw(flag)) for c in TRADABLE), draw(st.sampled_from(MODES)))
    filters = (replace(LIQUID, enabled=draw(flag)), replace(tradable, enabled=draw(flag)))
    filters += (CROSS_SECTIONAL["condition"],) if cross == "condition" else ()
    return Strategy(
        filters=filters,
        factors=factors,
        top_n=top_n,
        backups=draw(st.integers(0, 2)) if top_n else 0,
        period=draw(st.sampled_from(PERIODS)),
        every=draw(st.integers(1, 3)),
        weighting=weighting,
        formula=CLOSE if weighting == "formula" else None,
        band=draw(st.none() | st.sampled_from([0.0, 0.05, 0.5])),
        hold_rank=draw(st.none() | st.integers(top_n + 1, top_n + 3)) if top_n else None,
        max_weight=draw(st.none() | st.sampled_from([0.15, 0.3, 0.6])),
        pool=draw(pools()),
    )


@given(strategies())
def test_runs_keep_the_engine_invariants(panel, strategy):
    backtest = Backtest(strategy, cash=1_000_000, execution=Execution(slippage_bps=0.0), benchmark=None)
    result, again = run(backtest, panel), run(backtest, panel)
    ledger, decisions = result.ledger, result.funnel["date"].unique(maintain_order=True)
    check_equity(ledger)
    check_orders(panel, result, decisions)
    assert again.id == result.id
    for frame in ("daily", "positions", "orders"):
        assert getattr(again.ledger, frame).equals(getattr(ledger, frame))
    cache = Cache()
    for day in decisions:
        screened = screen(strategy, panel, day, cache)
        on_day = {
            frame: getattr(result, frame).filter(pl.col("date") == day) for frame in ("picks", "funnel", "detail")
        }
        assert all(frame.equals(getattr(again, name).filter(pl.col("date") == day)) for name, frame in on_day.items())
        considered = on_day["detail"].filter(pl.col("universe")).drop(HOLDINGS)
        assert screened.detail.drop(HOLDINGS).equals(considered)
        if strategy.hold_rank is None:
            assert screened.picks.equals(on_day["picks"]) and screened.funnel.equals(on_day["funnel"])
        else:
            assert rechosen(strategy, panel, result, day).equals(on_day["picks"])


def rechosen(strategy, panel, result, day):
    """The picks of day chosen again from its cross-section and the run's holdings at its close."""
    section = select(strategy, panel, [panel.row(day)])
    codes = set(result.ledger.positions.filter(pl.col("date") == day)["code"])
    held = np.array([code in codes for code in panel.codes.tolist()])
    return picks_frame(section, stack([choose(strategy, section, 0, held)]), panel)


def check_equity(ledger):
    """equity == cash + the value of the positions on every day."""
    values = ledger.positions.group_by("date").agg(pl.col("value").sum())
    days = ledger.daily.join(values, on="date", how="left").with_columns(pl.col("value").fill_null(0.0))
    assert np.allclose(days["equity"], days["cash"] + days["value"], rtol=1e-12, atol=1e-6)


def check_orders(panel, result, decisions):
    """Orders only on the day after a decision, never for Beijing, never for a held code without a bar that day."""
    rows = np.searchsorted(panel.dates, result.ledger.orders["date"].to_numpy()) - 1
    decided = set(np.searchsorted(panel.dates, decisions.to_numpy()).tolist())
    assert set(rows.tolist()) <= decided
    assert not result.ledger.orders["code"].str.ends_with(".BJ").any()
    column = {code: j for j, code in enumerate(panel.codes.tolist())}
    held = set(result.ledger.positions.select("date", "code").iter_rows())
    targets = set(result.picks.filter(pl.col("role") == "target").select("date", "code").iter_rows())
    for row, code in zip(rows.tolist(), result.ledger.orders["code"]):
        day = panel.dates[row].item()
        if (day, code) in held and not panel.traded[row, column[code]]:
            assert (day, code) in targets
