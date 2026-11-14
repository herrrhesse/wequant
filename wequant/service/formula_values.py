"""A formula condition's lines on a decision day (S4.1): the variables its output line uses, then its numbers, on the
bar the condition read, for an explanation or a stock the condition turned away."""

from __future__ import annotations

from typing import Literal

import numpy as np

from wequant.expr import Cache, Expr, evaluate_all, panel_key
from wequant.formula import compile_formula
from wequant.panel import Panel
from wequant.service.base import Model

DIRECT, TOTAL = 8, 12  # at most this many of the output line's variables, and of lines in all

Line = tuple[str, str, bool, np.ndarray]  # name, kind, direct, (N,) values on the day


class FormulaLine(Model):
    """One line shown for a formula condition."""

    name: str
    kind: Literal["num", "bool"]
    direct: bool
    """The output line uses it: XG: A AND B gives A and B."""


class FormulaValue(FormulaLine):
    """One line of a formula condition on the day, for one stock."""

    value: float | None
    """A condition's is 1 or 0; null: no value on that bar."""


def shown(expr: Expr) -> list[tuple[str, Expr, bool]]:
    """(name, tree on the formula's bars, direct) of the lines shown for a condition written as a formula: the output
    line's variables first, then the other numbers in the order they are defined; none for a condition written as a
    tree."""
    if expr.source is None:
        return []
    compiled = compile_formula(expr.source.text, expr.source.period)
    trees = {v.name: v.expr for v in compiled.variables if v.expr is not None}
    direct = [name for name in compiled.direct if name in trees][:DIRECT]
    numbers = [name for name, tree in trees.items() if tree.kind == "num" and name not in direct]
    return [(name, trees[name], True) for name in direct] + [(name, trees[name], False) for name in numbers][
        : TOTAL - len(direct)
    ]


def lines(expr: Expr, panel: Panel, row: int, cache: Cache | None = None) -> list[Line]:
    """Each shown line's values on decision row row, for every stock: a weekly or monthly formula's on the bar the
    stock read that day; kept in cache by panel, row and tree."""
    found = shown(expr)
    if not found:
        return []
    period = expr.source.period
    bars = panel if period == "day" else panel.periods(period)
    if not len(bars.dates):
        return [(name, tree.kind, direct, np.full(len(panel.codes), np.nan)) for name, tree, direct in found]
    ident = f"{panel_key(bars)} @{row}"
    vectors = {name: cache.get(f"{ident} {tree.key}") if cache is not None else None for name, tree, _ in found}
    missing = {name: tree for name, tree, _ in found if vectors[name] is None}
    if missing:
        read = np.full(len(panel.codes), row) if bars is panel else bars.sources()[row]
        for name, grid in evaluate_all(missing, bars).items():
            vector = np.where(read >= 0, grid[np.maximum(read, 0), np.arange(len(panel.codes))], np.nan)
            vectors[name] = vector
            if cache is not None:
                cache.put(f"{ident} {missing[name].key}", vector)
    return [(name, tree.kind, direct, vectors[name]) for name, tree, direct in found]


def values_at(found: list[Line], col: int) -> list[FormulaValue]:
    """The lines with their values for one stock."""
    return [FormulaValue(name=name, kind=kind, direct=direct, value=value) for (name, kind, direct, _), value in
            zip(found, numbers_at(found, col))]  # fmt: skip


def header(found: list[Line]) -> list[FormulaLine]:
    """The lines without values: what a screen gives once for every stock a condition turned away."""
    return [FormulaLine(name=name, kind=kind, direct=direct) for name, kind, direct, _ in found]


def numbers_at(found: list[Line], col: int) -> list[float | None]:
    """The lines' values for one stock, in the lines' order."""
    return [float(v[col]) if np.isfinite(v[col]) else None for _, _, _, v in found]
