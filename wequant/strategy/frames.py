"""Decisions as frames: picks, the funnel, every considered or held stock's verdicts and action, one day's rows and the
stocks each funnel level rejects."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import polars as pl

from wequant.strategy.choose import Choice
from wequant.strategy.compile import pool_items
from wequant.strategy.section import Section
from wequant.strategy.strategy import Strategy

if TYPE_CHECKING:
    from wequant.panel.panel import Panel

PICKS = {
    "date": pl.Date,
    "code": pl.String,
    "role": pl.String,
    "rank": pl.Int64,
    "score": pl.Float64,
    "weight": pl.Float64,
    "raw_weight": pl.Float64,
    "capped": pl.Boolean,
}
FUNNEL = {"date": pl.Date, "step": pl.String, "passed": pl.Int64, "left": pl.Int64}
REJECTS = {
    "code": pl.String,
    "step": pl.String,
    "kind": pl.String,
    "passed": pl.Boolean,
    "value": pl.Float64,
    "missing": pl.String,
}
ACTIONS = ("buy", "keep", "sell", "wait", "none")
SELL_KINDS = ("universe", "filter", "no_score", "rank", "slots")
BUY, KEEP, SELL, WAIT, NONE = range(len(ACTIONS))
SELL_COLUMNS = {"sell_kind": pl.Enum(SELL_KINDS), "sell_step": pl.String, "sell_rank": pl.Int64, "sell_limit": pl.Int64}


class Level(NamedTuple):
    """A level of the funnel's chain: its step name and kind."""

    step: str
    kind: str  # listed, bar, universe, pool, condition, group or scored


def levels(strategy: Strategy) -> list[Level]:
    """The chain levels in funnel order: listed, bar, universe, each pool item, each chain step, scored."""
    steps = [Level(s.name, "condition" if s.expr is not None else "group") for s in strategy.steps() if s.chain]
    pool = [Level(name, "pool") for name in pool_items(strategy)]
    first = [Level("listed", "listed"), Level("bar", "bar"), Level("universe", "universe")]
    return [*first, *pool, *steps, Level("scored", "scored")]


def first_failure(strategy: Strategy, section: Section) -> np.ndarray:
    """(R, N) index into levels(strategy) of the first level each stock fails; len(levels) for a scored stock."""
    verdicts = [section.steps[level.step] == 1 for level in levels(strategy) if level.step in section.steps]
    return _first_false([section.listed, section.traded, *verdicts, section.scored])


def picks_frame(section: Section, choice: Choice, panel: Panel) -> pl.DataFrame:
    """PICKS: each decision row's targets by rank, then its backups by rank."""
    i, j = np.nonzero(choice.target | choice.backup)
    order = np.lexsort((section.rank[i, j], ~choice.target[i, j], i))
    i, j = i[order], j[order]
    target = choice.target[i, j]
    columns = {
        "date": panel.dates[section.rows[i]],
        "code": panel.codes[j],
        "role": np.where(target, "target", "backup"),
        "rank": section.rank[i, j],
        "score": section.score[i, j],
        "weight": _floats(np.where(target, choice.weight[i, j], np.nan)),
        "raw_weight": _floats(np.where(target, choice.raw_weight[i, j], np.nan)),
        "capped": choice.capped[i, j],
    }
    return pl.DataFrame(columns, schema=PICKS)


def funnel_frame(strategy: Strategy, section: Section, choice: Choice, panel: Panel) -> pl.DataFrame:
    """FUNNEL: each decision row's steps in order."""
    names, passed, left = zip(*_funnel(strategy, section, choice))
    columns = {
        "date": np.repeat(panel.dates[section.rows], len(names)),
        "step": list(names) * len(section.rows),
        "passed": np.stack(passed, axis=1).ravel(),
        "left": _floats(np.stack(left, axis=1).ravel()),
    }
    return pl.DataFrame(columns).cast(FUNNEL)


def detail_frame(strategy: Strategy, section: Section, choice: Choice, panel: Panel) -> pl.DataFrame:
    """Each decision row's universe codes and held codes: verdicts, scored, rank, held, the action and why a sell."""
    cells = np.flatnonzero((section.steps["universe"] == 1) | choice.held)  # flat (row, column) indices, in order
    i, j = np.divmod(cells, len(panel.codes))
    verdicts = ["universe", *pool_items(strategy), *(step.name for step in strategy.steps())]
    action = _actions(*(np.take(mask, cells) for mask in (choice.target, choice.held, section.traded)))
    sold = np.flatnonzero(action == SELL)
    columns = {
        "date": pl.Series(panel.dates[section.rows], dtype=pl.Date).gather(i),
        "code": pl.Series(panel.codes, dtype=pl.String).gather(j),
        **{name: np.take(section.steps[name], cells) for name in verdicts},
        "scored": np.take(section.scored, cells),
        "rank": np.take(section.rank, cells),
        "held": np.take(choice.held, cells),
        "action": _names(action, ACTIONS, pl.Enum(ACTIONS)),
        **_sell_columns(strategy, section, choice, i[sold], j[sold], sold, len(cells)),
    }
    return pl.DataFrame(columns).with_columns(*_booleans(verdicts), _ranks())


def stocks_frame(strategy: Strategy, section: Section, choice: Choice, panel: Panel, i: int = 0) -> pl.DataFrame:
    """Every code's full row on decision row i."""
    steps = ["universe", *pool_items(strategy), *(step.name for step in strategy.steps())]
    target = choice.target[i]
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
        "role": _roles(target, choice.backup[i]),
        "weight": _floats(np.where(target, choice.weight[i], np.nan)),
        "raw_weight": _floats(np.where(target, choice.raw_weight[i], np.nan)),
        "capped": choice.capped[i],
    }
    return pl.DataFrame(columns).with_columns(*_booleans(steps), _ranks())


def rejects_frame(strategy: Strategy, section: Section, panel: Panel, i: int = 0) -> pl.DataFrame:
    """Each listed code without a score on decision row i, at the first chain level it fails: its verdict there (False,
    or None without a value), the value side of a simple comparison, and for the scored level the first missing value."""
    chain, failed = levels(strategy), first_failure(strategy, section)[i]
    j = np.flatnonzero((failed > 0) & (failed < len(chain)))
    at = [chain[k] for k in failed[j].tolist()]
    missing = _missing(strategy, section, np.full(len(j), i), j)
    rows = {
        "code": panel.codes[j].tolist(),
        "step": [lv.step for lv in at],
        "kind": [lv.kind for lv in at],
        "passed": [_verdict(section, lv, i, col, gone) for lv, col, gone in zip(at, j.tolist(), missing)],
        "value": [_value(section, lv.step, i, col) for lv, col in zip(at, j.tolist())],
        "missing": [gone if lv.kind == "scored" else None for lv, gone in zip(at, missing)],
    }
    return pl.DataFrame(rows, schema=REJECTS)


def _funnel(strategy: Strategy, section: Section, choice: Choice) -> Iterator[tuple[str, np.ndarray, np.ndarray]]:
    """(step, passed, left) per decision row, left along the chain (NaN in a group): a pool item's passed alone within
    the universe, a condition's or group's alone within the pool, every other level's equal to its left."""
    left = section.listed
    yield "listed", left.sum(axis=1), left.sum(axis=1)
    left = left & section.traded
    yield "bar", left.sum(axis=1), left.sum(axis=1)
    universe = left & (section.steps["universe"] == 1)
    left = universe
    yield "universe", left.sum(axis=1), left.sum(axis=1)
    for name in pool_items(strategy):
        passes = universe & (section.steps[name] == 1)
        left = left & passes
        yield name, passes.sum(axis=1), left.sum(axis=1)
    pool = left
    for step in strategy.steps():
        passes = pool & (section.steps[step.name] == 1)
        if step.chain:
            left = left & passes
            yield step.name, passes.sum(axis=1), left.sum(axis=1)
        else:
            yield step.name, passes.sum(axis=1), np.full(len(section.rows), np.nan)
    for name, mask in (("scored", section.scored), ("targets", choice.target), ("backups", choice.backup)):
        yield name, mask.sum(axis=1), mask.sum(axis=1)


def _actions(target: np.ndarray, held: np.ndarray, traded: np.ndarray) -> np.ndarray:
    """ACTIONS index of each cell: a target is kept or bought; a held other one is sold, or waits without a bar."""
    return np.select([target & held, target, held & traded, held], [KEEP, BUY, SELL, WAIT], NONE).astype(np.int8)


def _sell_columns(
    strategy: Strategy, section: Section, choice: Choice, i: np.ndarray, j: np.ndarray, at: np.ndarray, rows: int
) -> dict[str, pl.Series]:
    """SELL_COLUMNS of `rows` frame rows: the reasons of the sold cells (i, j) at rows `at`, null elsewhere."""
    columns = {name: pl.repeat(None, rows, dtype=dtype, eager=True) for name, dtype in SELL_COLUMNS.items()}
    if len(at):
        reasons = zip(*_sell_reasons(strategy, section, choice, i, j))
        for (name, dtype), values in zip(SELL_COLUMNS.items(), reasons):
            columns[name].scatter(at, pl.Series(values, dtype=dtype))
    return columns


def _sell_reasons(
    strategy: Strategy, section: Section, choice: Choice, i: np.ndarray, j: np.ndarray
) -> Iterator[tuple[str, str | None, int | None, int | None]]:
    """(kind, step, rank, limit) of each sold cell, by the first that applies: universe, filter, no_score, rank, slots."""
    parts = ["universe", *pool_items(strategy)]
    names = parts + [step.name for step in strategy.steps() if step.chain]
    first = _first_false([section.steps[name][i, j] == 1 for name in names]).tolist()
    missing = _missing(strategy, section, i, j)
    cells = zip(first, missing, section.scored[i, j].tolist(), choice.slots[i, j].tolist(), section.rank[i, j].tolist())
    for k, gone, scored, slots, rank in cells:
        if k < len(parts):
            yield "universe", names[k] if k else None, None, None
        elif k < len(names):
            yield "filter", names[k], None, None
        elif not scored:
            yield "no_score", gone, None, None
        elif slots:
            yield "slots", None, rank, strategy.top_n
        else:
            yield "rank", None, rank, strategy.hold_rank or strategy.top_n


def _missing(strategy: Strategy, section: Section, i: np.ndarray, j: np.ndarray) -> list[str]:
    """The first enabled factor without a finite value at each cell, else "formula"."""
    names = [f.name for f in strategy.factors if f.enabled]
    if not names:
        return ["formula"] * len(i)
    first = _first_false([np.isfinite(section.factors[name][i, j]) for name in names])
    return [names[k] if k < len(names) else "formula" for k in first.tolist()]


def _verdict(section: Section, level: Level, i: int, j: int, missing: str) -> bool | None:
    """A rejected cell's verdict at its level: False, or None where the level has no value there."""
    if level.kind == "bar":
        return False
    if level.kind == "scored":
        return False if missing == "formula" and np.isfinite(section.formula[i, j]) else None
    return None if section.steps[level.step][i, j] < 0 else False


def _value(section: Section, step: str, i: int, j: int) -> float | None:
    """The value side of a simple-comparison step at the cell, None without one."""
    value = section.values[step][i, j] if step in section.values else np.nan
    return float(value) if np.isfinite(value) else None


def _first_false(passes: list[np.ndarray]) -> np.ndarray:
    """Index of the first of passes that is False at each cell; len(passes) where all are True."""
    failed = ~np.stack(passes)
    return np.where(failed.any(axis=0), failed.argmax(axis=0), len(passes))


def _names(index: np.ndarray, names: tuple[str, ...], dtype: pl.DataType) -> pl.Series:
    """names[k] for each index k."""
    return pl.Series(index).replace_strict(dict(enumerate(names)), return_dtype=dtype)


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
