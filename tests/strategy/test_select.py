"""select and screen on a stored panel: one evaluation per root, the Beijing guard, and the frames' fixed schemas."""

from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from tests.account import golden
from tests.backtest.market import build_store
from wequant.expr import REF, Field, evaluate
from wequant.panel import load_panel
from wequant.strategy import (
    ACTIONS,
    PICKS,
    SELL_KINDS,
    Condition,
    Factor,
    Group,
    Pool,
    SpecError,
    Strategy,
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
    want = cross_section(STRATEGY, grids, rows, panel.listed[rows], panel.traded[rows])
    for name, value in want._asdict().items():
        if isinstance(value, dict):
            assert value.keys() == got._asdict()[name].keys()
            assert all(np.array_equal(v, got._asdict()[name][k], equal_nan=True) for k, v in value.items())
        else:
            assert np.array_equal(value, getattr(got, name), equal_nan=True), name
    assert got.steps["cheap"].dtype == np.int8 and got.factors["mom"].dtype == np.float64
    assert list(got.steps) == ["universe", "cheap", "up", "big", "g"] and list(got.factors) == ["mom", "level"]


def test_detail_keeps_its_schema_on_a_day_with_an_empty_universe(panel):
    nobody = replace(STRATEGY, universe=CLOSE > 1000.0)
    detail = screen(nobody, panel).detail
    assert detail.is_empty() and detail.schema == pl.Schema(
        {"date": pl.Date, "code": pl.String}
        | dict.fromkeys(("universe", "cheap", "up", "big", "g", "scored"), pl.Boolean)
        | {"rank": pl.Int64, "held": pl.Boolean, "action": pl.Enum(ACTIONS), "sell_kind": pl.Enum(SELL_KINDS)}
        | {"sell_step": pl.String, "sell_rank": pl.Int64, "sell_limit": pl.Int64}
    )
    assert screen(nobody, panel).picks.is_empty() and screen(nobody, panel).picks.schema == pl.Schema(PICKS)


def test_stocks_frame_holds_every_code_in_column_order(panel):
    """A screen's stocks frame: every code in column order with its verdicts, values, score and role; a code without
    a bar has none of them. A screen defaults to the panel's last day."""
    stocks = screen(STRATEGY, panel, golden.DAYS[5]).stocks  # 688001.SH has no bar that day
    assert stocks.columns == ["code", "universe", "cheap", "up", "big", "g", "scored", "mom", "level", "formula"] + [
        "score",
        "rank",
        "role",
        "weight",
        "raw_weight",
        "capped",
    ]
    assert stocks["code"].to_list() == panel.codes.tolist()
    star = stocks.row(by_predicate=pl.col("code") == golden.C, named=True)
    assert star["universe"] is None and star["cheap"] is None and star["g"] is False
    assert star["scored"] is False and star["rank"] is None and star["score"] is None and star["mom"] is None
    assert star["role"] is None and star["weight"] is None and star["raw_weight"] is None and star["capped"] is False
    assert stocks.schema["rank"] == pl.Int64 and stocks.schema["role"] == pl.String
    assert screen(STRATEGY, panel).funnel["date"].unique().to_list() == [panel.dates[-1].item()]
    roles = Strategy(factors=(Factor("level", CLOSE, prefer="low"),), top_n=1, backups=1)
    assert screen(roles, panel, golden.DAYS[3]).stocks.select("code", "role", "rank", "weight").rows() == [
        (golden.B, "target", 1, 1.0),
        (golden.A, "backup", 2, None),
        (golden.C, None, None, None),  # no bar that day
    ]


def test_a_universe_admitting_a_beijing_code_raises(tmp_path):
    days = ["2024-01-02", "2024-01-03"]
    bars = {"600000.SH": [(10.0, 10.0)] * 2, "830799.BJ": [(5.0, 5.0)] * 2}
    store = build_store(tmp_path / "home", days, bars, boards={"830799.BJ": "bse"})
    panel = load_panel(store=store, cache_dir=tmp_path / "cache")
    factors = (Factor("c", CLOSE),)
    with pytest.raises(SpecError, match="^universe: admits 830799.BJ on 2024-01-02") as raised:
        select(Strategy(universe=CLOSE > 0.0, factors=factors, top_n=1), panel, [0, 1])
    assert (raised.value.code, raised.value.loc) == ("admits_beijing", ("universe",))
    with pytest.raises(SpecError, match="^pool: admits 830799.BJ on 2024-01-03"):
        select(Strategy(universe=CLOSE > 0.0, factors=factors, top_n=1, pool=Pool(min_list_days=2)), panel, [1])
    assert select(Strategy(factors=factors, top_n=1), panel, [0, 1]).scored.tolist() == [[True, False]] * 2
    assert select(Strategy(universe=CLOSE > 0.0, pool=Pool(boards=("main",))), panel, [1]).scored.tolist() == [
        [True, False]
    ]
