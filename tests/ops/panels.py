"""Hypothesis strategies for operator inputs: grids whose NaN runs look like listings, suspensions and delistings.

Hypothesis draws shapes, windows and one seed per input; the seed's generator fills in the cells, which keeps each
example to a handful of draws.
"""

from __future__ import annotations

import numpy as np
from hypothesis import strategies as st

MAX_ROWS, MAX_COLS, MAX_N = 40, 5, 8
SUSPENSION = (0.0, 0.1, 0.5, 0.9)  # chance that a listed day has no bar
TIES = (0.0, 0.2, 0.8)  # share of small integers, which make ties
TRUE = (0.0, 0.2, 0.5, 1.0)  # chance that a condition is true once it can be


def rngs() -> st.SearchStrategy[np.random.Generator]:
    return st.integers(0, 2**32 - 1).map(np.random.default_rng)


def grids(rows: int, cols: int, dense: bool = False) -> st.SearchStrategy[np.ndarray]:
    """Values i / 1000 with |i| <= 10**7 mixed with small integers; NaN where there is no bar (nowhere when dense)."""
    return rngs().map(lambda rng: _with_gaps(rng, numbers(rng, rows, cols), dense))


def conditions(rows: int, cols: int, dense: bool = False) -> st.SearchStrategy[np.ndarray]:
    """Conditions from truths; NaN where there is no bar (nowhere when dense)."""
    return rngs().map(lambda rng: _with_gaps(rng, truths(rng, rows, cols), dense))


def scalars() -> st.SearchStrategy[float]:
    return st.one_of(st.integers(-3, 3).map(float), st.integers(-(10**7), 10**7).map(lambda i: i / 1000))


def argument(role: str, rows: int, cols: int, dense: bool = False) -> st.SearchStrategy:
    """x a grid, c a condition, y a grid with its own gaps or a scalar."""
    if role == "x":
        return grids(rows, cols, dense)
    if role == "c":
        return conditions(rows, cols, dense)
    return st.one_of(grids(rows, cols, dense), scalars())


@st.composite
def cases(draw, roles: str, minimum: int | None, min_rows: int = 0, dense: bool = False) -> tuple[tuple, dict]:
    """Arguments for an operator whose inputs have these roles, and its window n as keywords."""
    rows, cols = draw(st.integers(min_rows, MAX_ROWS)), draw(st.integers(1, MAX_COLS))
    arguments = tuple(draw(argument(role, rows, cols, dense)) for role in roles)
    return arguments, ({} if minimum is None else {"n": draw(st.integers(minimum, MAX_N))})


def numbers(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    """Finite values i / 1000 with |i| <= 10**7, mixed with small integers."""
    small = rng.random((rows, cols)) < rng.choice(TIES)
    fine = rng.integers(-(10**7), 10**7, (rows, cols), endpoint=True) / 1000
    return np.where(small, rng.integers(-3, 4, (rows, cols)), fine)


def truths(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    """Finite conditions: 0, or true as 1 or another nonzero number; some columns are never true or only late."""
    start = _often_zero(rng, rows, cols)
    true = (np.arange(rows)[:, None] >= start) & (rng.random((rows, cols)) < rng.choice(TRUE, cols))
    other = np.where(rng.random((rows, cols)) < 0.2, rng.choice([-2.5, 0.5, 7.0], (rows, cols)), 1.0)
    return np.where(true, other, 0.0)


def bars(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    """Where each column has a bar: between a leading and a trailing run, suspended inside; sometimes never."""
    day = np.arange(rows)[:, None]
    listed = (day >= _often_zero(rng, rows, cols)) & (day < rows - _often_zero(rng, rows, cols))
    return listed & (rng.random((rows, cols)) >= rng.choice(SUSPENSION, cols))


def _often_zero(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    """A run length per column: 0 half the time, else uniform in 0..rows."""
    return np.where(rng.random(cols) < 0.5, 0, rng.integers(0, rows, cols, endpoint=True))


def _with_gaps(rng: np.random.Generator, values: np.ndarray, dense: bool) -> np.ndarray:
    return values if dense else np.where(bars(rng, *values.shape), values, np.nan)
