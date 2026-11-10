"""Engine invariants over random strategies on a six-code market with gaps and a Beijing code."""

from dataclasses import replace

import numpy as np
import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.backtest.market import MIXED_BOARDS, build_store, mixed_market
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.expr import REF, STD, Cache, Field
from wequant.panel import load_panel
from wequant.strategy import MODES, PERIODS, Condition, Factor, Group, Strategy, screen

ADJ, CLOSE, OPEN = Field("adj_close"), Field("close"), Field("open")
FACTORS = (
    Factor("mom", ADJ / REF(ADJ, 3) - 1),
    Factor("calm", STD(ADJ / REF(ADJ, 1) - 1, 5), prefer="low"),
    Factor("cheap", CLOSE, prefer="low", weight=2),
)
LIQUID = Condition("liquid", Field("amount") > 1e6)
TRADABLE = (Condition("rising", CLOSE > OPEN), Condition("not_up", ~Field("limit_up")))


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("mixed")
    store = build_store(home / "home", *mixed_market(), boards=MIXED_BOARDS)
    return load_panel(store=store, cache_dir=home / "cache")


@st.composite
def strategies(draw) -> Strategy:
    flag = st.booleans()
    factors = tuple(replace(f, enabled=draw(flag)) for f in FACTORS)
    ranked = any(f.enabled for f in factors)
    top_n = draw(st.none() | st.integers(1, 5)) if ranked else None
    weighting = draw(st.sampled_from(["equal", "formula", *(["score"] if ranked else [])]))
    tradable = Group("tradable", tuple(replace(c, enabled=draw(flag)) for c in TRADABLE), draw(st.sampled_from(MODES)))
    return Strategy(
        filters=(replace(LIQUID, enabled=draw(flag)), replace(tradable, enabled=draw(flag))),
        factors=factors,
        top_n=top_n,
        backups=draw(st.integers(0, 2)) if top_n else 0,
        period=draw(st.sampled_from(PERIODS)),
        every=draw(st.integers(1, 3)),
        weighting=weighting,
        formula=CLOSE if weighting == "formula" else None,
        band=draw(st.none() | st.sampled_from([0.0, 0.05, 0.5])),
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
        for frame in ("picks", "funnel", "detail"):
            on_day = getattr(result, frame).filter(pl.col("date") == day)
            assert getattr(screened, frame).equals(on_day) and on_day.equals(
                getattr(again, frame).filter(pl.col("date") == day)
            )


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
