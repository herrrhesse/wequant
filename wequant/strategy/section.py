"""A strategy's cross-section from its evaluated grids: who passes, how they score, the targets, weights and backups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple

import numpy as np

from wequant import ops
from wequant.strategy.compile import pool_items
from wequant.strategy.strategy import Strategy


def verdicts(grid: np.ndarray) -> np.ndarray:
    """A condition grid as int8: 1 passes, 0 fails, -1 no value."""
    return np.where(np.isnan(grid), -1, grid == 1).astype(np.int8)


class Section(NamedTuple):
    """A strategy's cross-section on decision rows; every array is (R, N) over the rows and the panel's columns."""

    rows: np.ndarray  # (R,) int64 panel rows
    steps: dict[str, np.ndarray]  # int8 verdicts: "universe", each pool item, then each enabled step
    factors: dict[str, np.ndarray]  # float64: each enabled factor's value
    formula: np.ndarray | None  # float64: the weight formula's value, for weighting "formula"
    values: dict[str, np.ndarray]  # float64: the value side of each simple-comparison condition, when select keeps it
    listed: np.ndarray  # bool: the code is in its listed span on the decision row (descriptive)
    traded: np.ndarray  # bool: a bar on the decision row
    eligible: np.ndarray  # bool: the universe, every pool item and every chain step pass
    scored: np.ndarray  # bool: eligible, traded, every factor finite, a finite positive formula
    score: np.ndarray  # float64 in (0, 1] where scored, NaN elsewhere
    rank: np.ndarray  # int64 1-based among scored by (score desc, column asc), 0 elsewhere
    order: np.ndarray  # int32 columns by rank, the scored first


def cross_section(
    strategy: Strategy,
    grids: Mapping[str, np.ndarray],
    rows: np.ndarray,
    listed: np.ndarray,
    traded: np.ndarray,
    values: Mapping[str, np.ndarray] | None = None,
) -> Section:
    """The Section of decision rows from grids keyed as expressions(strategy) on those rows: verdicts for the universe,
    pool items and conditions, float64 for factors and the formula; listed and traded are those rows' masks."""
    steps = _steps(strategy, grids)
    eligible = _eligible(strategy, steps)
    factors = {f.name: grids[f.name] for f in strategy.factors if f.enabled}
    formula = grids["formula"] if strategy.weighting == "formula" else None
    scored = _scored(eligible & traded, factors.values(), formula)
    score = _score(strategy, factors, scored)
    order = np.argsort(-score, axis=1, kind="stable").astype(np.int32)  # NaN sorts last
    rank = _rank(order, scored)
    rows = np.asarray(rows, dtype=np.int64)
    return Section(
        rows, steps, factors, formula, dict(values or {}), listed, traded, eligible, scored, score, rank, order
    )


def _steps(strategy: Strategy, grids: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The universe's, each pool item's and each step's verdicts; a group combines its members' pass and fail, never
    their values."""
    steps = {name: grids[name] for name in ("universe", *pool_items(strategy))}
    for step in strategy.steps():
        if step.expr is not None:
            steps[step.name] = grids[step.name]
        else:
            passes = np.stack([steps[member] == 1 for member in step.members])
            steps[step.name] = (passes.all(axis=0) if step.mode == "all" else passes.any(axis=0)).astype(np.int8)
    return steps


def _eligible(strategy: Strategy, steps: dict[str, np.ndarray]) -> np.ndarray:
    """The universe, every pool item and every chain step pass."""
    eligible = steps["universe"] == 1
    for name in (*pool_items(strategy), *(step.name for step in strategy.steps() if step.chain)):
        eligible = eligible & (steps[name] == 1)
    return eligible


def _scored(candidates: np.ndarray, factors: Iterable[np.ndarray], formula: np.ndarray | None) -> np.ndarray:
    """candidates whose factors are all finite and whose formula, if any, is finite and positive."""
    scored = candidates
    for value in factors:
        scored = scored & np.isfinite(value)
    return scored if formula is None else scored & np.isfinite(formula) & (formula > 0)


def weight_shares(strategy: Strategy) -> dict[str, float]:
    """Each enabled factor's weight / the enabled factors' total weight, in factor order: its share of the score."""
    enabled = [f for f in strategy.factors if f.enabled]
    total = sum(f.weight for f in enabled)
    return {f.name: f.weight / total for f in enabled}


def _score(strategy: Strategy, factors: dict[str, np.ndarray], scored: np.ndarray) -> np.ndarray:
    """The weighted mean of the factors' percentiles among the scored stocks; 1.0 for each without a factor.

    Equal to the percentiles summed by weight_shares up to rounding; dividing once keeps archived scores bit-identical.
    """
    enabled = [f for f in strategy.factors if f.enabled]
    if not enabled:
        return np.where(scored, 1.0, np.nan)
    total = np.zeros(scored.shape)
    for f in enabled:
        value = factors[f.name] if f.prefer == "high" else -factors[f.name]
        total += f.weight * ops.CS_PCT(np.where(scored, value, np.nan))
    return total / sum(f.weight for f in enabled)


def _rank(order: np.ndarray, scored: np.ndarray) -> np.ndarray:
    """1-based rank by score descending, ties to the lower column; 0 where not scored."""
    rank = np.empty(order.shape, dtype=np.int64)
    np.put_along_axis(rank, order, np.broadcast_to(np.arange(1, order.shape[1] + 1), order.shape), axis=1)
    return np.where(scored, rank, 0)
