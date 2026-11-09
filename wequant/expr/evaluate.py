"""Evaluating expressions on a panel: each distinct subexpression once, root results cached."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

import numpy as np

from wequant.expr.cache import Cache, panel_key
from wequant.expr.tree import Expr, Field, Op
from wequant.expr.vocab import FAMILIES, FIELDS, OPERATORS

if TYPE_CHECKING:
    from wequant.panel import Panel


def evaluate(expr: Expr, panel: Panel, cache: Cache | None = None) -> np.ndarray:
    """expr on panel: a read-only (T, N) float64 grid aligned to panel.dates × panel.codes."""
    return evaluate_all({"": expr}, panel, cache)[""]


def evaluate_all(exprs: Mapping[str, Expr], panel: Panel, cache: Cache | None = None) -> dict[str, np.ndarray]:
    """Each named expression as by evaluate; a subexpression used more than once among them is computed once."""
    ident, roots = panel_key(panel), {e.key: e for e in exprs.values()}
    memo: dict[str, np.ndarray] = {}
    if cache is not None:
        memo = {key: grid for key in roots if (grid := cache.get(f"{ident} {key}")) is not None}
    todo = [e for key, e in roots.items() if key not in memo]
    keep = _repeated(todo) | {e.key for e in todo}
    for root in todo:
        _value(root, panel, memo, keep)
        if cache is not None:
            cache.put(f"{ident} {root.key}", memo[root.key])
    return {name: memo[e.key] for name, e in exprs.items()}


def _repeated(roots: Iterable[Expr]) -> set[str]:
    """Keys of the nodes that occur more than once among the trees of roots; a repeated node's subtree counts once."""
    counts: Counter[str] = Counter()
    stack = list(roots)
    while stack:
        node = stack.pop()
        counts[node.key] += 1
        if counts[node.key] == 1 and isinstance(node, Op):
            stack.extend(a for a in node.args if isinstance(a, Expr))
    return {key for key, n in counts.items() if n > 1}


def _value(node: Expr | float | int | None, panel: Panel, memo: dict, keep: set[str]) -> np.ndarray | float | int:
    """A read-only grid for an Expr, kept in memo when its key is in keep; NaN for None; a number as it is."""
    if not isinstance(node, Expr):
        return np.nan if node is None else node
    grid = memo.get(node.key)
    if grid is None:
        grid = _load(node.name, panel) if isinstance(node, Field) else _call(node, panel, memo, keep)
        grid.flags.writeable = False
        if node.key in keep:
            memo[node.key] = grid
    return grid


def _call(node: Op, panel: Panel, memo: dict, keep: set[str]) -> np.ndarray:
    return OPERATORS[node.name].function(*[_value(a, panel, memo, keep) for a in node.args])


def _load(name: str, panel: Panel) -> np.ndarray:
    if name in FIELDS:
        return FIELDS[name].load(panel)
    family, _, param = name.partition("@")
    return FAMILIES[family].load(panel, param)
