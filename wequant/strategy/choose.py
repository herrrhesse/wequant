"""The targets of one decision row given the holdings at its close: who to hold, at what weight, and the stand-ins."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

from wequant.strategy.section import Section
from wequant.strategy.strategy import Strategy

ROUNDING = 1e-12  # targets x cap equal to the total leaves ~1e-16 of excess, not cash


class Choice(NamedTuple):
    """One decision row's choice: masks over the panel's columns and the weights of its targets in column order;
    stack() makes the masks (R, N), the weights those of every row's targets in turn, and cash (R,)."""

    held: np.ndarray  # bool: shares at the decision day's close
    target: np.ndarray  # bool
    raw_weight: np.ndarray  # float64 weight of equity before max_weight, one per target
    weight: np.ndarray  # float64 weight after max_weight, one per target, each > 0
    capped: np.ndarray  # bool: a target max_weight cut, directly or after taking an excess
    backup: np.ndarray  # bool: stands in, best rank first, for a new target that gets no shares
    slots: np.ndarray  # bool: held and within hold_rank but left out, as top_n held stocks rank higher
    cash: np.ndarray  # float64 (): the weight max_weight left in cash because no target could take it


def choose(strategy: Strategy, section: Section, i: int, held: np.ndarray) -> Choice:
    """Decision row i given the codes held at its close: without hold_rank the top_n ranks whatever is held; with it a
    held stock stays while ranked within hold_rank, the best top_n of them, and new stocks fill the rest by rank."""
    held = np.asarray(held, dtype=bool)
    ranked = section.order[i, : section.scored[i].sum()]  # scored columns, best rank first
    target, slots = _targets(strategy, ranked, held)
    if strategy.hold_rank is None:
        stand_ins = ranked[target[ranked].sum() :]
    else:
        stand_ins = ranked[~held[ranked] & ~target[ranked]]
    backup = _mask(held.shape, stand_ins[: strategy.backups])
    raw = _weights(strategy, section, i, target)
    weight, capped, cash = _capped(raw, strategy.max_weight)
    return Choice(held, target, raw[target], weight[target], capped, backup, slots, np.float64(cash))


def stack(choices: Sequence[Choice]) -> Choice:
    """The choices of successive decision rows as one Choice: the weights one after another, all else stacked."""
    held, target, raw, weight, capped, backup, slots, cash = zip(*choices)
    return Choice(
        np.stack(held),
        np.stack(target),
        np.concatenate(raw),
        np.concatenate(weight),
        np.stack(capped),
        np.stack(backup),
        np.stack(slots),
        np.stack(cash),
    )


def _targets(strategy: Strategy, ranked: np.ndarray, held: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Target and slots masks from the scored columns in rank order."""
    n = len(ranked) if strategy.top_n is None else strategy.top_n
    if strategy.hold_rank is None:
        return _mask(held.shape, ranked[:n]), np.zeros(held.shape, dtype=bool)
    within = ranked[: strategy.hold_rank]
    kept = within[held[within]]
    fill = ranked[~held[ranked]][: max(n - len(kept), 0)]
    return _mask(held.shape, np.concatenate([kept[:n], fill])), _mask(held.shape, kept[n:])


def _weights(strategy: Strategy, section: Section, i: int, target: np.ndarray) -> np.ndarray:
    """Weights in slots of 1/top_n (1/targets without top_n): one slot each, or n slots shared by score or formula;
    unfilled slots stay in cash."""
    n = target.sum()
    slot = 1 / (max(n, 1) if strategy.top_n is None else strategy.top_n)
    if strategy.weighting == "equal":
        return np.where(target, slot, 0.0)
    value = np.where(target, section.score[i] if strategy.weighting == "score" else section.formula[i], 0.0)
    total = value.sum()
    return n * slot * value / total if total > 0 else np.zeros_like(value)


def _capped(weight: np.ndarray, cap: float | None) -> tuple[np.ndarray, np.ndarray, float]:
    """weight with none above cap: each round cuts the targets above it and shares the excess among the others in
    proportion to their weight; an excess no target can take stays in cash."""
    capped = np.zeros(weight.shape, dtype=bool)
    if cap is None:
        return weight, capped, 0.0
    weight = weight.copy()
    while (over := weight > cap).any():
        excess = float((weight[over] - cap).sum())
        weight[over], capped = cap, capped | over
        free = (weight > 0) & ~capped
        if not free.any():
            return weight, capped, excess if excess > ROUNDING else 0.0
        weight[free] += excess * weight[free] / weight[free].sum()
    return weight, capped, 0.0


def _mask(shape: tuple[int, ...], columns: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[columns] = True
    return mask
