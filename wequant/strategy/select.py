"""A strategy's cross-section on a panel: its expressions evaluated once on the whole panel, kept on decision rows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import polars as pl

from wequant.expr import Cache, Expr, evaluate
from wequant.strategy.section import Section, cross_section, verdicts
from wequant.strategy.strategy import Strategy

if TYPE_CHECKING:
    from numpy.typing import ArrayLike

    from wequant.panel.panel import Day, Panel

PICKS = {
    "date": pl.Date,
    "code": pl.String,
    "role": pl.String,
    "rank": pl.Int64,
    "score": pl.Float64,
    "weight": pl.Float64,
}
FUNNEL = {"date": pl.Date, "step": pl.String, "passed": pl.Int64, "left": pl.Int64}


class Screen(NamedTuple):
    """One day's decision: the picks, funnel and detail a backtest records for it, and every stock's full row."""

    picks: pl.DataFrame
    funnel: pl.DataFrame
    detail: pl.DataFrame
    stocks: pl.DataFrame


def expressions(strategy: Strategy) -> dict[str, Expr]:
    """The roots by name: "universe", each enabled condition, each enabled factor, "formula"."""
    roots = {"universe": strategy.universe}
    roots |= {step.name: step.expr for step in strategy.steps() if step.expr is not None}
    roots |= {f.name: f.expr for f in strategy.factors if f.enabled}
    if strategy.weighting == "formula":
        roots["formula"] = strategy.formula
    return roots


def select(
    strategy: Strategy,
    panel: Panel,
    rows: ArrayLike,
    cache: Cache | None = None,
    on_root: Callable[[str], None] | None = None,
) -> Section:
    """Each root evaluated on the whole panel and kept on rows, on_root(name) after each; ValueError when the universe
    admits a Beijing code on one of them."""
    rows = np.asarray(rows, dtype=np.int64)
    values = {f.name for f in strategy.factors if f.enabled} | {"formula"}
    grids = {}
    for name, expr in expressions(strategy).items():
        grid = evaluate(expr, panel, cache)[rows]
        grids[name] = grid if name in values else verdicts(grid)  # 1 byte a cell instead of 8
        if on_root is not None:
            on_root(name)
    _check_beijing(panel, rows, grids["universe"])
    return cross_section(strategy, grids, panel.traded[rows], rows)


def screen(strategy: Strategy, panel: Panel, day: Day | None = None, cache: Cache | None = None) -> Screen:
    """The decision at day's close (default the panel's last day): exactly what a backtest on this panel records for
    that day."""
    check_anchor(strategy, panel)
    row = len(panel.dates) - 1 if day is None else panel.row(day)
    section = select(strategy, panel, [row], cache)
    return Screen(
        picks_frame(section, panel),
        funnel_frame(strategy, section, panel),
        detail_frame(strategy, section, panel),
        stocks_frame(strategy, section, panel),
    )


def check_anchor(strategy: Strategy, panel: Panel) -> None:
    """ValueError when the strategy declares an anchor and the panel starts on another day."""
    if strategy.anchor is not None and panel.dates[0] != np.datetime64(strategy.anchor, "D"):
        raise ValueError(f"anchor: the strategy counts from {strategy.anchor}, the panel from {panel.dates[0]}")


def picks_frame(section: Section, panel: Panel) -> pl.DataFrame:
    """PICKS: targets then backups of each decision row, by date then rank."""
    target = section.weight > 0
    i, j = np.nonzero(target | section.backup)
    order = np.lexsort((section.rank[i, j], i))
    i, j = i[order], j[order]
    columns = {
        "date": panel.dates[section.rows[i]],
        "code": panel.codes[j],
        "role": np.where(target[i, j], "target", "backup"),
        "rank": section.rank[i, j],
        "score": section.score[i, j],
        "weight": _floats(np.where(target[i, j], section.weight[i, j], np.nan)),
    }
    return pl.DataFrame(columns, schema=PICKS)


def funnel_frame(strategy: Strategy, section: Section, panel: Panel) -> pl.DataFrame:
    """FUNNEL: each decision row's steps in order."""
    names, passed, left = zip(*_funnel(strategy, section))
    columns = {
        "date": np.repeat(panel.dates[section.rows], len(names)),
        "step": list(names) * len(section.rows),
        "passed": np.stack(passed, axis=1).ravel(),
        "left": _floats(np.stack(left, axis=1).ravel()),
    }
    return pl.DataFrame(columns).cast(FUNNEL)


def detail_frame(strategy: Strategy, section: Section, panel: Panel) -> pl.DataFrame:
    """Each decision row's universe codes: step verdicts, scored and rank."""
    i, j = np.nonzero(section.steps["universe"] == 1)
    steps = [step.name for step in strategy.steps()]
    columns = {
        "date": pl.Series(panel.dates[section.rows], dtype=pl.Date).gather(i),
        "code": pl.Series(panel.codes, dtype=pl.String).gather(j),
        **{name: section.steps[name][i, j] for name in steps},
        "scored": section.scored[i, j],
        "rank": section.rank[i, j],
    }
    return pl.DataFrame(columns).with_columns(*_booleans(steps), _ranks())


def stocks_frame(strategy: Strategy, section: Section, panel: Panel, i: int = 0) -> pl.DataFrame:
    """Every code's full row on decision row i."""
    steps = ["universe", *(step.name for step in strategy.steps())]
    target, backup = section.weight[i] > 0, section.backup[i]
    values = dict(section.factors)
    if section.formula is not None:
        values["formula"] = section.formula
    columns = {
        "code": pl.Series(panel.codes, dtype=pl.String),
        **{name: section.steps[name][i] for name in steps},
        "scored": section.scored[i],
        **{name: _floats(value[i]) for name, value in values.items()},
        "score": _floats(section.score[i]),
        "rank": section.rank[i],
        "role": _roles(target, backup),
        "weight": _floats(np.where(target, section.weight[i], np.nan)),
    }
    return pl.DataFrame(columns).with_columns(*_booleans(steps), _ranks())


def _funnel(strategy: Strategy, section: Section) -> Iterator[tuple[str, np.ndarray, np.ndarray]]:
    """(step, passed, left) per decision row: passed alone within the universe, left along the chain (NaN in a group)."""
    universe = section.steps["universe"] == 1
    left = universe
    yield "universe", universe.sum(axis=1), universe.sum(axis=1)
    for step in strategy.steps():
        passes = universe & (section.steps[step.name] == 1)
        if step.chain:
            left = left & passes
            yield step.name, passes.sum(axis=1), left.sum(axis=1)
        else:
            yield step.name, passes.sum(axis=1), np.full(len(section.rows), np.nan)
    for name, mask in (("scored", section.scored), ("targets", section.weight > 0), ("backups", section.backup)):
        yield name, mask.sum(axis=1), mask.sum(axis=1)


def _check_beijing(panel: Panel, rows: np.ndarray, universe: np.ndarray) -> None:
    beijing = panel.exchanges == "BJ"
    i, j = np.nonzero(universe[:, beijing] == 1)
    if i.size:
        code, day = panel.codes[beijing][j[0]], panel.dates[rows[i[0]]]
        raise ValueError(f"universe admits {code} on {day}: the rule book has no Beijing rules; add ~exchange@BJ")


def _floats(values: np.ndarray) -> pl.Series:
    """values as Float64, null for NaN."""
    return pl.Series(values, dtype=pl.Float64).fill_nan(None)


def _roles(target: np.ndarray, backup: np.ndarray) -> pl.Series:
    return pl.Series(["target" if t else "backup" if b else None for t, b in zip(target, backup)], dtype=pl.String)


def _booleans(names: list[str]) -> list[pl.Expr]:
    """Verdict columns as Boolean: null where the condition has no value (-1)."""
    return [pl.when(pl.col(name) >= 0).then(pl.col(name) == 1).alias(name) for name in names]


def _ranks() -> pl.Expr:
    return pl.when(pl.col("rank") > 0).then(pl.col("rank")).alias("rank")
