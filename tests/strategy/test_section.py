"""The cross-section on one decision row, from hand-made grids: scores, ranks, the group rule, and the weights and
backups choose takes from it without holdings."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from wequant.expr import Field
from wequant.strategy import (
    Condition,
    Factor,
    Group,
    Strategy,
    choose,
    cross_section,
    detail_frame,
    funnel_frame,
    stack,
    verdicts,
)
from wequant.strategy.section import percentiles, weight_shares

NAN = np.nan
CLOSE = Field("close")
UNIVERSE = [1, 1, 1, 1, 0]
X1, X2 = [3, 1, 3, NAN, 5], [10, 20, 30, 40, 50]
TWO = Strategy(
    filters=(Condition("ok", CLOSE > 0.0),),
    factors=(Factor("x1", CLOSE), Factor("x2", CLOSE, prefer="low", weight=2)),
    top_n=2,
    backups=1,
)
STAND_IN = SimpleNamespace(dates=np.array(["2024-01-02"], dtype="datetime64[D]"), codes=np.array(list("ABCDE")))


def row(values):
    return np.array([values], dtype=np.float64)


def section(strategy, universe=UNIVERSE, conditions=None, traded=None, **values):
    """cross_section of one row: condition lists become verdicts, values float grids; every stock listed and, unless
    traded says otherwise, traded."""
    grids = {name: verdicts(row(v)) for name, v in ({"universe": universe} | (conditions or {})).items()}
    grids |= {name: row(v) for name, v in values.items()}
    every = np.ones((1, len(universe)), dtype=bool)
    return cross_section(strategy, grids, np.array([0]), every, every if traded is None else traded)


def chosen(strategy, got):
    """choose on the one row of got without holdings, stacked."""
    return stack([choose(strategy, got, 0, np.zeros(got.scored.shape[1], dtype=bool))])


def weights(choice):
    """The choice's target weights on its grid, 0 elsewhere."""
    grid = np.zeros(choice.target.shape)
    grid[choice.target] = choice.weight
    return grid


def two(strategy=TWO, **extra):
    return section(strategy, conditions={"ok": [1] * 5}, x1=X1, x2=X2, **extra)


def test_scores_rank_backup_and_equal_weights():
    got = two()
    assert got.scored.tolist() == [[True, True, True, False, False]]
    assert got.score[0, :3].tolist() == [0.9444444444444445, 0.5555555555555555, 0.5]
    assert np.isnan(got.score[0, 3:]).all()
    assert got.rank.tolist() == [[1, 2, 3, 0, 0]] and got.rank.dtype == np.int64
    assert got.order[0, :3].tolist() == [0, 1, 2]
    assert chosen(TWO, got).backup.tolist() == [[False, False, True, False, False]]
    assert weights(chosen(TWO, got)).tolist() == [[0.5, 0.5, 0, 0, 0]]
    assert got.eligible.tolist() == [[True, True, True, True, False]]


def test_percentiles_rank_the_value_and_what_the_score_ranks_among_the_scored():
    pct = percentiles(TWO, two())
    assert np.allclose(pct["x1"][0][0, :3], [5 / 6, 1 / 3, 5 / 6]) and np.allclose(
        pct["x1"][1][0, :3], [5 / 6, 1 / 3, 5 / 6]
    )
    assert np.allclose(pct["x2"][0][0, :3], [1 / 3, 2 / 3, 1]) and np.allclose(pct["x2"][1][0, :3], [1, 2 / 3, 1 / 3])
    assert np.isnan(pct["x1"][0][0, 3:]).all()  # D has no x1, E is outside the universe


def test_the_score_weighs_each_factor_by_its_weight_share():
    got, shares = two(), weight_shares(TWO)
    pct = percentiles(TWO, got)
    expected = shares["x1"] * pct["x1"][1] + shares["x2"] * pct["x2"][1]
    assert shares == {"x1": 1 / 3, "x2": 2 / 3}
    assert np.allclose(got.score[0, :3], expected[0, :3], rtol=0, atol=1e-15)


def test_score_and_formula_weights():
    """n slots shared by score or by formula; a stock whose formula is not a finite positive number is not scored."""
    by_score = replace(TWO, weighting="score")
    assert weights(chosen(by_score, two(by_score))).tolist() == [[0.6296296296296297, 0.3703703703703703, 0, 0, 0]]
    by_formula = replace(TWO, weighting="formula", formula=CLOSE)
    assert weights(chosen(by_formula, two(by_formula, formula=[2, 6, 1, 1, 1]))).tolist() == [[0.25, 0.75, 0, 0, 0]]
    got = two(by_formula, formula=[2, -1, NAN, 1, 1])
    assert got.scored.tolist() == [[True, False, False, False, False]]
    assert weights(chosen(by_formula, got)).tolist() == [[0.5, 0, 0, 0, 0]]


def test_ties_go_to_the_lower_column():
    one = Strategy(factors=(Factor("x", CLOSE),), top_n=4)
    got = section(one, universe=[1, 1, 1, 1], x=[2, 1, 2, 1])
    assert got.score.tolist() == [[0.875, 0.375, 0.875, 0.375]] and got.rank.tolist() == [[1, 3, 2, 4]]


def test_unfilled_slots_stay_in_cash():
    five = replace(TWO, top_n=5)
    assert weights(chosen(five, two(five))).tolist() == [[0.2, 0.2, 0.2, 0, 0]]
    by_score = replace(five, weighting="score")
    assert weights(chosen(by_score, two(by_score)))[0] == pytest.approx(
        [0.2833333333333334, 0.16666666666666666, 0.15000000000000002, 0, 0]
    )
    single = replace(five, factors=TWO.factors[:1], weighting="score")
    assert weights(chosen(single, two(single)))[0] == pytest.approx([0.25, 0.1, 0.25, 0, 0])


def test_without_a_factor_every_scored_stock_is_held_in_column_order():
    got = section(Strategy())
    assert weights(chosen(Strategy(), got)).tolist() == [[0.25] * 4 + [0]] and got.rank.tolist() == [[1, 2, 3, 4, 0]]
    assert got.score[0, :4].tolist() == [1.0] * 4


def test_a_stock_without_a_bar_is_not_scored():
    traded = np.array([[True, False, True, True, True]])
    got = two(traded=traded)
    assert got.scored.tolist() == [[True, False, True, False, False]] and got.rank.tolist() == [[1, 0, 2, 0, 0]]
    assert got.traded is traded


GROUP = Strategy(
    filters=(
        Group("either", (Condition("a", CLOSE > 0.0), Condition("b", CLOSE > 0.0)), mode="any"),
        Condition("d", CLOSE > 0.0),
    ),
    factors=(Factor("x1", CLOSE),),
    top_n=1,
)
A, B, D = [1, NAN, 0, 0, 1], [1, 1, 1, 0, 0], [1, 1, 0, 1, 1]


def grouped(universe):
    return section(GROUP, universe, {"a": A, "b": B, "d": D}, x1=[1, 2, 3, 4, 5])


def funnel_rows(got):
    return funnel_frame(GROUP, got, chosen(GROUP, got), STAND_IN).select("step", "passed", "left").rows()


@pytest.mark.parametrize(
    "universe, either, rank, funnel",
    [
        (
            [1] * 5,
            [1, 1, 1, 0, 1],
            [3, 2, 0, 0, 1],
            [("universe", 5, 5), ("a", 2, None), ("b", 3, None), ("either", 4, 4), ("d", 4, 3), ("scored", 3, 3)],
        ),
        (
            UNIVERSE,
            [1, 1, 1, 0, 1],
            [2, 1, 0, 0, 0],
            [("universe", 4, 4), ("a", 1, None), ("b", 3, None), ("either", 3, 3), ("d", 3, 2), ("scored", 2, 2)],
        ),
    ],
    ids=["all_in_the_universe", "e_outside_it"],
)
def test_a_group_combines_pass_and_fail_never_values(universe, either, rank, funnel):
    """A group of any passes when a member passes, a member without a value counting as a fail (A's a is NaN); a group
    member narrows nothing on its own; the funnel counts only universe stocks."""
    got = grouped(universe)
    assert got.steps["either"].tolist() == [either] and got.steps["either"].dtype == np.int8
    assert got.rank.tolist() == [rank]
    head, tail = [("listed", 5, 5), ("bar", 5, 5)], [("targets", 1, 1), ("backups", 0, 0)]
    assert funnel_rows(got) == head + funnel + tail
    detail = detail_frame(GROUP, got, chosen(GROUP, got), STAND_IN)
    inside = [k for k, u in enumerate(universe) if u == 1]
    assert detail["code"].to_list() == ["ABCDE"[k] for k in inside]
    assert detail.select("a", "b", "either", "d").to_dict(as_series=False) == {
        "a": [[True, None, False, False, True][k] for k in inside],
        "b": [[True, True, True, False, False][k] for k in inside],
        "either": [bool(either[k]) for k in inside],
        "d": [[True, True, False, True, True][k] for k in inside],
    }
