"""A strategy's cross-section on a panel: its compiled roots evaluated once on the whole panel, kept on decision rows."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import polars as pl

from wequant.expr import Cache, Expr, evaluate, evaluate_all
from wequant.strategy.choose import Choice, choose, stack
from wequant.strategy.compile import comparison, contains, expressions, pool_items
from wequant.strategy.errors import SpecError
from wequant.strategy.frames import detail_frame, funnel_frame, picks_frame, rejects_frame, stocks_frame
from wequant.strategy.section import Section, cross_section, verdicts
from wequant.strategy.strategy import Strategy

if TYPE_CHECKING:
    from numpy.typing import ArrayLike

    from wequant.panel.panel import Day, Panel


class Screen(NamedTuple):
    """One day's decision without holdings: the picks, funnel and detail a backtest records for it when nothing is
    held, every stock's full row, the stocks each funnel level rejects, and the section and choice they come from."""

    picks: pl.DataFrame
    funnel: pl.DataFrame
    detail: pl.DataFrame
    stocks: pl.DataFrame
    rejects: pl.DataFrame
    section: Section
    choice: Choice


def select(
    strategy: Strategy,
    panel: Panel,
    rows: ArrayLike,
    cache: Cache | None = None,
    on_root: Callable[[str], None] | None = None,
    values: bool = False,
) -> Section:
    """Each root of expressions(strategy) evaluated on the whole panel and kept on rows, on_root(name) after each: the
    pool and its parts together, then every other root, seeded with the pool's grid when one has it wrapped in, so the
    pool is computed once; values keeps each simple comparison's value side too. SpecError admits_beijing when the
    pool admits a Beijing code on one of the rows."""
    rows, roots, report = np.asarray(rows, dtype=np.int64), expressions(strategy), on_root or (lambda name: None)
    family = {name: roots[name] for name in ("universe", *pool_items(strategy), "pool")}
    wrapped = any(contains(expr, family["pool"]) for name, expr in roots.items() if name not in family)
    kept, seed = _pool_family(family, panel, rows, cache, wrapped)
    for name in family:
        report(name)
    _check_beijing(panel, rows, kept["pool"], "pool" if len(family) > 2 else "universe")
    numbers, sides = {f.name for f in strategy.factors if f.enabled} | {"formula"}, {}
    for name, expr in roots.items():
        if name not in family:
            grid, side = _evaluate(expr, values and name not in numbers, panel, cache, seed)
            kept[name] = grid[rows] if name in numbers else verdicts(grid[rows])
            if side is not None:
                sides[name] = side[rows]
            report(name)
    return cross_section(strategy, kept, rows, panel.listed[rows], panel.traded[rows], sides)


def screen(strategy: Strategy, panel: Panel, day: Day | None = None, cache: Cache | None = None) -> Screen:
    """The decision at day's close (default the panel's last day) without holdings: exactly what a backtest on this
    panel records for that day when it holds nothing."""
    check_anchor(strategy, panel)
    row = len(panel.dates) - 1 if day is None else panel.row(day)
    section = select(strategy, panel, [row], cache, values=True)
    choice = stack([choose(strategy, section, 0, np.zeros(len(panel.codes), dtype=bool))])
    return Screen(
        picks_frame(section, choice, panel),
        funnel_frame(strategy, section, choice, panel),
        detail_frame(strategy, section, choice, panel),
        stocks_frame(strategy, section, choice, panel),
        rejects_frame(strategy, section, panel),
        section,
        choice,
    )


def check_anchor(strategy: Strategy, panel: Panel) -> None:
    """SpecError not_a_trading_day when the strategy declares an anchor and the panel starts on another day."""
    if strategy.anchor is not None and panel.dates[0] != np.datetime64(strategy.anchor, "D"):
        message = f"the strategy counts from {strategy.anchor}, the panel from {panel.dates[0]}"
        raise SpecError("not_a_trading_day", ("anchor",), message)


def _pool_family(
    family: dict[str, Expr], panel: Panel, rows: np.ndarray, cache: Cache | None, wrapped: bool
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """The universe's, the pool items' and the pool's verdicts on rows, and the pool's grid as a seed when wrapped
    says a later root needs it; no other full grid outlives the call."""
    grids = evaluate_all(family, panel, cache)
    seed = {family["pool"].key: grids["pool"]} if wrapped else {}
    return {name: verdicts(grid[rows]) for name, grid in grids.items()}, seed  # 1 byte a cell instead of 8


def _evaluate(
    expr: Expr, side: bool, panel: Panel, cache: Cache | None, seed: dict
) -> tuple[np.ndarray, np.ndarray | None]:
    """expr's grid and, when side asks and expr is a simple comparison, its value side's grid, sharing subexpressions."""
    compared = comparison(expr) if side else None
    if compared is None:
        return evaluate(expr, panel, cache, seed), None
    grids = evaluate_all({"condition": expr, "side": compared.side}, panel, cache, seed)
    return grids["condition"], grids["side"]


def _check_beijing(panel: Panel, rows: np.ndarray, pool: np.ndarray, name: str) -> None:
    beijing = panel.exchanges == "BJ"
    i, j = np.nonzero(pool[:, beijing] == 1)
    if i.size:
        code, day = panel.codes[beijing][j[0]], panel.dates[rows[i[0]]]
        message = f"admits {code} on {day}: the rule book has no Beijing rules; add ~exchange@BJ"
        raise SpecError("admits_beijing", (name,), message)
