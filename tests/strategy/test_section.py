"""The cross-section on one decision row, from hand-made grids: scores, ranks, weights, backups and the group rule."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from wequant.expr import Field
from wequant.strategy import Condition, Factor, Group, Strategy, cross_section, detail_frame, funnel_frame, verdicts

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


def section(strategy, universe=UNIVERSE, conditions=None, **values):
    """cross_section of one row: condition lists become verdicts, values float grids; every stock traded."""
    grids = {name: verdicts(row(v)) for name, v in ({"universe": universe} | (conditions or {})).items()}
    grids |= {name: row(v) for name, v in values.items()}
    return cross_section(strategy, grids, np.ones((1, len(universe)), dtype=bool), np.array([0]))


def two(strategy=TWO, **extra):
    return section(strategy, conditions={"ok": [1] * 5}, x1=X1, x2=X2, **extra)


def test_verdicts_pass_only_on_one():
    assert verdicts(row([1, 0, NAN, 0.5])).tolist() == [[1, 0, -1, 0]]
    assert verdicts(row([1])).dtype == np.int8


def test_scores_rank_backup_and_equal_weights():
    got = two()
    assert got.scored.tolist() == [[True, True, True, False, False]]
    assert got.score[0, :3].tolist() == [0.9444444444444445, 0.5555555555555555, 0.5]
    assert np.isnan(got.score[0, 3:]).all()
    assert got.rank.tolist() == [[1, 2, 3, 0, 0]] and got.rank.dtype == np.int64
    assert got.backup.tolist() == [[False, False, True, False, False]]
    assert got.weight.tolist() == [[0.5, 0.5, 0, 0, 0]]
    assert got.eligible.tolist() == [[True, True, True, True, False]]


def test_score_and_formula_weights():
    assert two(replace(TWO, weighting="score")).weight.tolist() == [[0.6296296296296297, 0.3703703703703703, 0, 0, 0]]
    by_formula = replace(TWO, weighting="formula", formula=CLOSE)
    assert two(by_formula, formula=[2, 6, 1, 1, 1]).weight.tolist() == [[0.25, 0.75, 0, 0, 0]]


def test_formula_weighting_leaves_a_stock_with_no_positive_formula_unscored():
    by_formula = replace(TWO, weighting="formula", formula=CLOSE)
    got = two(by_formula, formula=[2, -1, NAN, 1, 1])
    assert got.scored.tolist() == [[True, False, False, False, False]] and got.weight.tolist() == [[0.5, 0, 0, 0, 0]]


def test_ties_go_to_the_lower_column():
    one = Strategy(factors=(Factor("x", CLOSE),), top_n=4)
    got = section(one, universe=[1, 1, 1, 1], x=[2, 1, 2, 1])
    assert got.score.tolist() == [[0.875, 0.375, 0.875, 0.375]] and got.rank.tolist() == [[1, 3, 2, 4]]


def test_unfilled_slots_stay_in_cash():
    five = replace(TWO, top_n=5)
    assert two(five).weight.tolist() == [[0.2, 0.2, 0.2, 0, 0]]
    assert two(replace(five, weighting="score")).weight[0] == pytest.approx(
        [0.2833333333333334, 0.16666666666666666, 0.15000000000000002, 0, 0]
    )
    single = replace(five, factors=TWO.factors[:1], weighting="score")
    assert two(single).weight[0] == pytest.approx([0.25, 0.1, 0.25, 0, 0])


def test_without_a_factor_every_scored_stock_is_held_in_column_order():
    got = section(Strategy())
    assert got.weight.tolist() == [[0.25] * 4 + [0]] and got.rank.tolist() == [[1, 2, 3, 4, 0]]
    assert got.score[0, :4].tolist() == [1.0] * 4


def test_a_stock_without_a_bar_is_not_scored():
    grids = {"universe": verdicts(row(UNIVERSE)), "ok": verdicts(row([1] * 5)), "x1": row(X1), "x2": row(X2)}
    traded = np.array([[True, False, True, True, True]])
    got = cross_section(TWO, grids, traded, np.array([0]))
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
    return funnel_frame(GROUP, got, STAND_IN).select("step", "passed", "left").rows()


def test_a_group_combines_pass_and_fail_never_values():
    got = grouped([1] * 5)
    assert got.steps["either"].tolist() == [[1, 1, 1, 0, 1]] and got.steps["either"].dtype == np.int8
    assert got.eligible.tolist() == [[True, True, False, False, True]]
    assert got.rank.tolist() == [[3, 2, 0, 0, 1]] and got.weight.tolist() == [[0, 0, 0, 0, 1.0]]
    assert funnel_rows(got) == [
        ("universe", 5, 5),
        ("a", 2, None),
        ("b", 3, None),
        ("either", 4, 4),
        ("d", 4, 3),
        ("scored", 3, 3),
        ("targets", 1, 1),
        ("backups", 0, 0),
    ]
    detail = detail_frame(GROUP, got, STAND_IN)
    assert detail["code"].to_list() == list("ABCDE")
    assert detail.select("a", "b", "either", "d", "scored", "rank").to_dict(as_series=False) == {
        "a": [True, None, False, False, True],
        "b": [True, True, True, False, False],
        "either": [True, True, True, False, True],
        "d": [True, True, False, True, True],
        "scored": [True, True, False, False, True],
        "rank": [3, 2, None, None, 1],
    }


def test_a_group_counts_only_universe_stocks():
    got = grouped(UNIVERSE)
    assert got.rank.tolist() == [[2, 1, 0, 0, 0]] and got.weight.tolist() == [[0, 1.0, 0, 0, 0]]
    assert funnel_rows(got) == [
        ("universe", 4, 4),
        ("a", 1, None),
        ("b", 3, None),
        ("either", 3, 3),
        ("d", 3, 2),
        ("scored", 2, 2),
        ("targets", 1, 1),
        ("backups", 0, 0),
    ]
    detail = detail_frame(GROUP, got, STAND_IN)
    assert detail["code"].to_list() == list("ABCD")
    assert detail.schema == pl.Schema(
        {"date": pl.Date, "code": pl.String, "a": pl.Boolean, "b": pl.Boolean, "either": pl.Boolean}
        | {"d": pl.Boolean, "scored": pl.Boolean, "rank": pl.Int64}
    )
