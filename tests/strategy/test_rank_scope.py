"""Cross-sectional ranks within the pool (D13): a hand-computed golden, and order independence as a property."""

from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.backtest.market import MIXED_BOARDS, build_store, mixed_market
from wequant.expr import CS_PCT, CS_RANK, CS_ZSCORE, REF, Field, evaluate
from wequant.panel import load_panel
from wequant.strategy import Condition, Factor, Group, Pool, Strategy, choose, expressions, screen, select, stack

CLOSE, OPEN, ADJ = Field("close"), Field("open"), Field("adj_close")
A, B, C, D = "600000.SH", "000001.SZ", "600004.SH", "000002.SZ"  # columns in code order: B, D, A, C
DAYS = ["2024-01-02", "2024-01-03"]
CLOSES = {A: 10.0, B: 20.0, C: 30.0, D: 5.0}  # D is ST
CHEAP, RANKED = Condition("cheap", CLOSE < 25), Condition("ranked", CS_RANK(CLOSE) >= 2)
GOLDEN = Strategy(  # cheap, before the rank, rejects C: the rank must not notice
    filters=(CHEAP, RANKED), factors=(Factor("level", CS_RANK(CLOSE)),), top_n=1, pool=Pool(exclude_st=True)
)
NAN = np.nan


def chosen(strategy, section):
    """The choices of every row of section without holdings."""
    nothing = np.zeros(section.scored.shape[1], dtype=bool)
    return stack([choose(strategy, section, i, nothing) for i in range(len(section.rows))])


def weights(choice):
    """The choice's target weights on its grid, 0 elsewhere."""
    grid = np.zeros(choice.target.shape)
    grid[choice.target] = choice.weight
    return grid


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("rank_scope")
    status = [{"code": code, "date": day, "tradestatus": 1, "is_st": code == D} for code in CLOSES for day in DAYS]
    bars = {code: [(close, close)] * 2 for code, close in CLOSES.items()}
    return load_panel(store=build_store(home / "home", DAYS, bars, status=status), cache_dir=home / "cache")


def test_a_rank_in_a_condition_counts_only_the_pool(panel):
    """D13: a cross-sectional rank in a condition or factor ranks the pool (universe and pool items) and nothing else:
    not the whole market (ST D out), not the stocks an earlier condition left (C is not cheap but still ranks 3)."""
    assert panel.codes.tolist() == [B, D, A, C]
    assert evaluate(CS_RANK(CLOSE), panel)[1].tolist() == [3, 1, 2, 4]  # the whole market: A would pass
    assert np.array_equal(evaluate(expressions(GOLDEN)["ranked"].args[0], panel)[1], [2, NAN, 1, 3], equal_nan=True)
    got = select(GOLDEN, panel, [1])
    assert got.steps["exclude_st"].tolist() == [[1, 0, 1, 1]] and got.steps["cheap"].tolist() == [[1, 1, 1, 0]]
    assert got.steps["ranked"].tolist() == [[1, -1, 0, 1]]  # among B, A, C: B 2, A 1, C 3
    assert np.array_equal(got.factors["level"], [[2, NAN, 1, 3]], equal_nan=True)
    assert got.scored.tolist() == [[True, False, False, False]]
    assert np.array_equal(got.score, [[1.0, NAN, NAN, NAN]], equal_nan=True)
    assert got.rank.tolist() == [[1, 0, 0, 0]] and weights(chosen(GOLDEN, got)).tolist() == [[1.0, 0, 0, 0]]
    funnel = screen(GOLDEN, panel, DAYS[1]).funnel.select("step", "passed", "left").rows()
    assert funnel == [
        ("listed", 4, 4),
        ("bar", 4, 4),
        ("universe", 4, 4),
        ("exclude_st", 3, 3),
        ("cheap", 2, 2),
        ("ranked", 2, 1),
        ("scored", 1, 1),
        ("targets", 1, 1),
        ("backups", 0, 0),
    ]


CONDITIONS = (
    Condition("top", CS_RANK(CLOSE) >= 2),
    Condition("busy", CS_PCT(Field("amount")) > 0.3),
    Condition("dear", CLOSE > 10.0),
    Condition("free", ~Field("limit_up")),
    Group("g", (Condition("z", CS_ZSCORE(CLOSE) > -1.0), Condition("rising", CLOSE > OPEN)), mode="any"),
)
FACTORS = (Factor("mom", CS_ZSCORE(ADJ / REF(ADJ, 3))), Factor("cheap", CLOSE, prefer="low"))


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    home = tmp_path_factory.mktemp("mixed")
    store = build_store(home / "home", *mixed_market(), boards=MIXED_BOARDS)
    return load_panel(store=store, cache_dir=home / "cache")


@st.composite
def orders(draw):
    """A strategy over a random subset of CONDITIONS and the same strategy with its filters in another order."""
    filters = draw(st.lists(st.sampled_from(CONDITIONS), min_size=2, max_size=5, unique=True))
    pool = Pool(exclude_st=draw(st.booleans()), min_list_days=draw(st.none() | st.integers(2, 8)))
    strategy = Strategy(filters=tuple(filters), factors=FACTORS, top_n=2, backups=1, weighting="score", pool=pool)
    return strategy, replace(strategy, filters=tuple(draw(st.permutations(filters))))


@given(orders())
def test_any_order_of_the_filters_selects_the_same(mixed, pair):
    rows = np.arange(len(mixed.dates))
    one, two = (select(strategy, mixed, rows) for strategy in pair)
    for name in ("scored", "score", "rank", "order"):
        assert np.array_equal(getattr(one, name), getattr(two, name), equal_nan=True), name
    assert all(np.array_equal(one.steps[name], two.steps[name]) for name in one.steps)
    assert all(np.array_equal(a, b) for a, b in zip(chosen(pair[0], one), chosen(pair[1], two)))
