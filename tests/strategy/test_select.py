"""select and screen on a stored panel: one evaluation per root, the Beijing guard, and the frames' fixed schemas."""

import datetime as dt
from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from tests.account import golden
from tests.backtest.market import build_store
from wequant.expr import REF, Cache, Field, evaluate
from wequant.panel import load_panel
from wequant.strategy import (
    FUNNEL,
    PICKS,
    Condition,
    Factor,
    Group,
    Strategy,
    check_anchor,
    cross_section,
    expressions,
    screen,
    select,
    verdicts,
)

ADJ, CLOSE, OPEN = Field("adj_close"), Field("close"), Field("open")
STRATEGY = Strategy(
    filters=(
        Condition("cheap", CLOSE < 9.5),
        Group("g", (Condition("up", CLOSE > OPEN), Condition("big", CLOSE > 20.0)), mode="any"),
    ),
    factors=(Factor("mom", ADJ / REF(ADJ, 1)), Factor("level", CLOSE, prefer="low", weight=2)),
    top_n=1,
    backups=1,
    weighting="formula",
    formula=OPEN,
)


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("golden")
    golden.build_store(home)
    return load_panel(store=golden.Store(home), cache_dir=home / "cache")


def test_select_is_cross_section_of_the_evaluated_rows(panel):
    rows = np.array([1, 4, 5, 12])
    got = select(STRATEGY, panel, rows)
    grids = {name: evaluate(expr, panel)[rows] for name, expr in expressions(STRATEGY).items()}
    grids |= {name: verdicts(grids[name]) for name in ("universe", "cheap", "up", "big")}
    want = cross_section(STRATEGY, grids, panel.traded[rows], rows)
    for name, value in want._asdict().items():
        if isinstance(value, dict):
            assert value.keys() == got._asdict()[name].keys()
            assert all(np.array_equal(v, got._asdict()[name][k], equal_nan=True) for k, v in value.items())
        else:
            assert np.array_equal(value, getattr(got, name), equal_nan=True), name
    assert got.steps["cheap"].dtype == np.int8 and got.factors["mom"].dtype == np.float64
    assert list(got.steps) == ["universe", "cheap", "up", "big", "g"] and list(got.factors) == ["mom", "level"]


def test_on_root_follows_expressions_and_a_cache_serves_the_second_select(panel):
    names, cache = [], Cache()
    select(STRATEGY, panel, [3, 8], cache, on_root=names.append)
    assert names == list(expressions(STRATEGY)) == ["universe", "cheap", "up", "big", "mom", "level", "formula"]
    assert (cache.hits, cache.misses) == (0, 7)
    select(STRATEGY, panel, [3, 8], cache)
    assert cache.hits == 7


def test_screen_defaults_to_the_last_day_and_keeps_the_schemas(panel):
    day = panel.dates[-1].item()
    got = screen(STRATEGY, panel)
    assert got.picks.schema == pl.Schema(PICKS) and got.funnel.schema == pl.Schema(FUNNEL)
    assert set(got.funnel["date"]) == {day} and got.funnel["step"].to_list()[0] == "universe"
    assert got.detail.equals(screen(STRATEGY, panel, day).detail)
    assert screen(STRATEGY, panel, golden.DAYS[3]).funnel["date"][0] == dt.date(2024, 3, 6)


def test_detail_keeps_its_schema_on_a_day_with_an_empty_universe(panel):
    nobody = replace(STRATEGY, universe=CLOSE > 1000.0)
    detail = screen(nobody, panel).detail
    assert detail.is_empty() and detail.schema == pl.Schema(
        {"date": pl.Date, "code": pl.String}
        | dict.fromkeys(("cheap", "up", "big", "g", "scored"), pl.Boolean)
        | {"rank": pl.Int64}
    )
    assert screen(nobody, panel).picks.is_empty() and screen(nobody, panel).picks.schema == pl.Schema(PICKS)


def test_stocks_frame_holds_every_code_in_column_order(panel):
    stocks = screen(STRATEGY, panel, golden.DAYS[5]).stocks  # 688001.SH has no bar that day
    assert stocks.columns == ["code", "universe", "cheap", "up", "big", "g", "scored", "mom", "level", "formula"] + [
        "score",
        "rank",
        "role",
        "weight",
    ]
    assert stocks["code"].to_list() == panel.codes.tolist()
    star = stocks.row(by_predicate=pl.col("code") == golden.C, named=True)
    assert star["universe"] is None and star["cheap"] is None and star["g"] is False
    assert star["scored"] is False and star["rank"] is None and star["score"] is None and star["mom"] is None
    assert star["role"] is None and star["weight"] is None
    assert stocks.schema["rank"] == pl.Int64 and stocks.schema["role"] == pl.String


def test_screen_reports_targets_and_backups(panel):
    strategy = Strategy(factors=(Factor("level", CLOSE, prefer="low"),), top_n=1, backups=1)
    stocks = screen(strategy, panel, golden.DAYS[3]).stocks
    assert stocks.select("code", "role", "rank", "weight").rows() == [
        (golden.B, "target", 1, 1.0),
        (golden.A, "backup", 2, None),
        (golden.C, None, None, None),  # no bar that day
    ]


def test_check_anchor(panel):
    check_anchor(replace(STRATEGY, anchor=dt.date(2024, 3, 1)), panel)
    with pytest.raises(ValueError, match="^anchor: "):
        check_anchor(replace(STRATEGY, anchor=dt.date(2024, 3, 4)), panel)
    with pytest.raises(ValueError, match="^anchor: "):
        screen(replace(STRATEGY, anchor=dt.date(2024, 3, 4)), panel)


def test_a_universe_admitting_a_beijing_code_raises(tmp_path):
    days = ["2024-01-02", "2024-01-03"]
    bars = {"600000.SH": [(10.0, 10.0)] * 2, "830799.BJ": [(5.0, 5.0)] * 2}
    store = build_store(tmp_path / "home", days, bars, boards={"830799.BJ": "bse"})
    panel = load_panel(store=store, cache_dir=tmp_path / "cache")
    factors = (Factor("c", CLOSE),)
    with pytest.raises(ValueError, match="^universe admits 830799.BJ on 2024-01-02"):
        select(Strategy(universe=CLOSE > 0.0, factors=factors, top_n=1), panel, [0, 1])
    assert select(Strategy(factors=factors, top_n=1), panel, [0, 1]).weight.tolist() == [[1, 0], [1, 0]]
