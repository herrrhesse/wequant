"""choose on hand-made cross-sections: the hold buffer, slots, backups, weights and the max_weight water level."""

import numpy as np
import pytest

from wequant.expr import Field
from wequant.strategy import Factor, Strategy, choose, cross_section, verdicts

CLOSE = Field("close")
BY_X = Strategy(factors=(Factor("x", CLOSE),), top_n=2, backups=1)


def section(strategy, x, formula=None):
    """The cross-section of one row whose stocks all pass and score by x, highest best."""
    x = np.array([x], dtype=np.float64)
    every = np.ones(x.shape, dtype=bool)
    grids = {"universe": verdicts(every.astype(float)), "x": x}
    if formula is not None:
        grids["formula"] = np.array([formula], dtype=np.float64)
    return cross_section(strategy, grids, np.array([0]), every, every)


def held(*columns, n=6):
    mask = np.zeros(n, dtype=bool)
    mask[list(columns)] = True
    return mask


X = [6, 5, 4, 3, 2, 1]  # column k ranks k + 1


def test_without_hold_rank_the_holdings_change_nothing():
    got = section(BY_X, X)
    nothing, everything = choose(BY_X, got, 0, held()), choose(BY_X, got, 0, np.ones(6, dtype=bool))
    for a, b in zip(nothing[1:], everything[1:]):
        assert np.array_equal(a, b)
    assert nothing.target.tolist() == [True, True, False, False, False, False]
    assert nothing.backup.tolist() == [False, False, True, False, False, False]
    assert nothing.held.tolist() == [False] * 6 and everything.held.all()


def test_a_held_stock_stays_while_within_hold_rank_and_new_ones_fill_by_rank():
    buffered = Strategy(factors=BY_X.factors, top_n=2, backups=2, hold_rank=4)
    got = choose(buffered, section(buffered, X), 0, held(3, 5))  # ranks 4 and 6
    assert got.target.tolist() == [True, False, False, True, False, False]  # rank 4 stays; rank 1 fills the slot
    assert got.backup.tolist() == [False, True, True, False, False, False]  # not held, not a target, by rank
    assert not got.slots.any() and got.weight.tolist() == [0.5, 0.5]


def test_held_stocks_beyond_top_n_within_hold_rank_are_left_out_for_slots():
    buffered = Strategy(factors=BY_X.factors, top_n=2, hold_rank=5)
    got = choose(buffered, section(buffered, X), 0, held(1, 2, 4))  # ranks 2, 3 and 5 all within hold_rank
    assert got.target.tolist() == [False, True, True, False, False, False]
    assert got.slots.tolist() == [False, False, False, False, True, False]


def test_a_held_backup_rank_is_skipped_with_hold_rank_but_not_without():
    buffered = Strategy(factors=BY_X.factors, top_n=1, backups=1, hold_rank=2)
    assert choose(buffered, section(buffered, X), 0, held(2)).backup.tolist() == [
        False,
        True,
        False,
        False,
        False,
        False,
    ]
    plain = Strategy(factors=BY_X.factors, top_n=1, backups=1)
    assert choose(plain, section(plain, X), 0, held(1)).backup.tolist() == [False, True, False, False, False, False]


def test_weights_follow_the_final_targets():
    """Score weights share the slots among the targets hold_rank leaves: rank 1 (score 1) and the kept rank 5 (score
    2/6) get 3/4 and 1/4."""
    by_score = Strategy(factors=BY_X.factors, top_n=2, weighting="score", hold_rank=5)
    got = choose(by_score, section(by_score, X), 0, held(4))  # rank 5 stays beside rank 1
    assert got.target.tolist() == [True, False, False, False, True, False]
    assert got.weight == pytest.approx([0.75, 0.25], abs=1e-15)


def test_the_excess_over_the_cap_goes_to_the_other_targets_in_proportion():
    capped = Strategy(factors=BY_X.factors, top_n=3, weighting="formula", formula=CLOSE, max_weight=0.4)
    got = choose(capped, section(capped, X, [6, 3, 1, 9, 9, 9]), 0, held())
    # A is cut to 0.4 and its 0.2 goes 3:1 to B and C (0.45, 0.15); B is cut to 0.4 and its 0.05 goes to C
    assert got.raw_weight.tolist() == [0.6, 0.3, 0.1] and got.target.tolist() == [True] * 3 + [False] * 3
    assert got.weight == pytest.approx([0.4, 0.4, 0.2], abs=1e-15)
    assert got.capped.tolist() == [True, True, False, False, False, False] and got.cash == 0.0


def test_what_no_target_can_take_stays_in_cash():
    """A cap excess no target can take stays in cash: two targets capped at 0.4 keep 0.2; equal weights are cut only
    when the cap is below one slot (0.3 leaves 1/4 slots alone, 0.2 keeps 0.2 in cash)."""
    capped = Strategy(factors=BY_X.factors, top_n=2, weighting="score", max_weight=0.4)
    got = choose(capped, section(capped, X), 0, held())
    assert got.weight.tolist() == [0.4, 0.4] and got.capped.tolist()[:2] == [True, True]
    assert got.cash == pytest.approx(0.2, abs=1e-15) and got.raw_weight.sum() - got.weight.sum() == pytest.approx(0.2)
    loose = Strategy(factors=BY_X.factors, top_n=4, max_weight=0.3)
    assert choose(loose, section(loose, X), 0, held()).weight.tolist() == [0.25] * 4
    tight = Strategy(factors=BY_X.factors, top_n=4, max_weight=0.2)
    got = choose(tight, section(tight, X), 0, held())
    assert got.weight.tolist() == [0.2] * 4 and got.cash == pytest.approx(0.2)


def test_unfilled_slots_are_not_cap_cash():
    """Slots no stock fills stay in cash but are not max_weight's cash: no max_weight_cash warning for them."""
    capped = Strategy(factors=BY_X.factors, top_n=4, weighting="score", max_weight=0.5)
    got = choose(capped, section(capped, [2, 1, np.nan, np.nan, np.nan, np.nan]), 0, held())
    assert got.weight.tolist() == [0.25 * 2 * 2 / 3, 0.25 * 2 / 3] and got.cash == 0.0


def test_a_cap_that_exactly_fits_every_target_leaves_no_cash():
    """28c2017: a rounding-level excess (targets x cap equal to the total) is not counted as cap cash."""
    exact = Strategy(factors=BY_X.factors, top_n=4, weighting="score", max_weight=0.25)
    got = choose(exact, section(exact, [4, 3, 2, 1, np.nan, np.nan]), 0, held())
    assert got.weight == pytest.approx([0.25] * 4) and got.cash == 0.0
