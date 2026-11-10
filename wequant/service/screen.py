"""screen: one decision day's cross-section of a strategy, the backtest's own path with no holdings."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import numpy as np
import polars as pl

from wequant.backtest import DataOptions, screen_warnings
from wequant.backtest.anomalies import screen_uses
from wequant.panel import NotInPanel, Panel, load_panel
from wequant.service.base import Context, DataRequest, Model, manifest, not_found
from wequant.service.details import Loc, spec_details, to_data, usage
from wequant.service.outputs import DataAnomaly, Op, Role, SavedRef, Warning, data_anomaly, warnings_out
from wequant.service.specs import DataSpec, IsoDate
from wequant.service.strategies import Hash, StrategyInput, resolve, saved_as
from wequant.strategy import (
    Choice,
    Section,
    SpecError,
    SpecErrors,
    Strategy,
    comparison,
    percentiles,
    pool_items,
    weight_shares,
)
from wequant.strategy import screen as core_screen
from wequant.strategy.library import strategy_hash

StepKind = Literal["listed", "bar", "universe", "pool", "condition", "group", "scored", "targets", "backups"]


class ScreenRequest(StrategyInput, DataRequest):
    """A strategy (inline or saved) on one decision day."""

    date: IsoDate | None = None
    """The decision day t; null: the store's last trading day."""
    data: DataSpec = DataSpec()


class FunnelStep(Model):
    """One level of the funnel, in order: listed, bar, universe, pool items, filters, scored, targets, backups."""

    step: str
    """The level's name, or the condition or group name."""
    kind: StepKind
    chain: bool
    """False for conditions inside a group, targets and backups."""
    passed: int
    """Passing alone: pool items within the universe, filters within the pool; else equal to left."""
    left: int | None
    """Left along the chain; on chain levels, rejects at level k = left[k-1] - left[k]; null off the chain."""
    mode: Literal["all", "any"] | None
    """Groups only."""
    members: list[str] | None
    """Groups only: member condition names."""


class FactorCell(Model):
    """One enabled factor of a scored stock."""

    name: str
    value: float
    pct: float
    """Percentile of value among the scored stocks, in (0, 1]."""
    adjusted_pct: float
    """The percentile of value, of -value for a factor preferring low values: what the score uses."""
    weight_share: float
    contribution: float
    """weight_share x adjusted_pct; the contributions add up to score."""


class ScreenRow(Model):
    """A scored stock, by rank."""

    code: str
    rank: int
    score: float
    """In (0, 1]."""
    role: Role
    weight: float | None
    """Target weight after the cap; null: not a target."""
    raw_weight: float | None
    """Target weight before the cap; null: not a target."""
    capped: bool
    factors: list[FactorCell]
    formula: float | None
    """The weight formula's value; null: not formula weighting."""
    values: dict[str, float | None]
    """Each simple comparison's expression side at t, by condition name; null: no value."""


class MemberResult(Model):
    """One condition of a group."""

    name: str
    passed: bool | None
    """null: no value, which fails too."""


class Reject(Model):
    """A listed stock without a score, at the first chain level it fails."""

    code: str
    step: str
    kind: StepKind
    passed: Literal[False] | None
    """False: failed; null: no value, which fails too."""
    value: float | None
    """A simple comparison's expression side; null otherwise or without a value."""
    op: Op | None
    """The comparison, the constant moved to the right; null: not a simple comparison."""
    threshold: float | None
    threshold_hi: float | None
    """between only."""
    members: list[MemberResult] | None
    """Groups only."""
    missing: str | None
    """At scored: the first factor without a value, or formula."""


class ScreenResult(Model):
    """One day's funnel, scored stocks and rejects; every listed stock appears once."""

    data_version: int
    date: dt.date
    next_day: dt.date | None
    """The fill day t+1; null: t is the store's last day."""
    strategy_hash: Hash
    saved_as: list[SavedRef]
    scored_count: int
    """N, the percentiles' denominator."""
    cap_cash: float
    """Share of equity left in cash by max_weight; 0: none."""
    funnel: list[FunnelStep]
    rows: list[ScreenRow]
    rejects: list[Reject]
    anomalies: DataAnomaly | None
    """Anomalous data the scored stocks' signals read; null: none."""
    warnings: list[Warning]


def screen(request: ScreenRequest, context: Context = Context()) -> ScreenResult:
    """One decision day's funnel, scored stocks and rejects without holdings: the backtest's own path."""
    library = context.library()
    strategy, warnings = resolve(request, library)
    data = to_data(request.data)
    context.stage("compile")
    panel = strategy_panel(strategy, request.data_version, data, context, _where(request))
    with on_panel(_where(request)):
        shot = core_screen(strategy, panel, request.date, context.expr_cache, context.mark)
    section, choice, row = shot.section, shot.choice, int(shot.section.rows[0])
    uses = screen_uses(strategy, panel, section, choice)
    found = strategy_hash(strategy)
    result = ScreenResult(
        data_version=panel.version,
        date=panel.dates[row].item(),
        next_day=panel.dates[row + 1].item() if row + 1 < len(panel.dates) else None,
        strategy_hash=found,
        saved_as=saved_as(library, found),
        scored_count=int(section.scored[0].sum()),
        cap_cash=float(choice.cash[0]),
        funnel=funnel_steps(strategy, shot.funnel),
        rows=screen_rows(strategy, section, choice, panel),
        rejects=reject_rows(strategy, shot.rejects, section, panel),
        anomalies=data_anomaly(uses, panel.excluded, sample=None),
        warnings=warnings + warnings_out(screen_warnings(strategy, panel, section, choice, uses)),
    )
    context.stage("result")
    return result


def strategy_panel(
    strategy: Strategy, version: int | None, data: DataOptions, context: Context, loc: Loc = ("strategy",)
) -> Panel:
    """The panel a backtest without an end runs on: every code from the strategy's anchor to the store's last day."""
    store = context.store()
    manifest(store, version)  # NOT_FOUND that names the kept versions
    context.stage("open_store")
    try:
        panel = load_panel(
            strategy.anchor, None, version=version, store=store, cache_dir=context.cache, exclude=data.excluded
        )
    except NotInPanel as error:
        raise not_found(error.code, error.args[0], [*loc, "anchor"]) from None
    context.stage("open_panel")
    return panel


@contextmanager
def on_panel(loc: Loc = ("strategy",)) -> Iterator[None]:
    """A strategy's refusal on a panel (anchor, Beijing codes) as USAGE located under loc; a day not on it NOT_FOUND."""
    try:
        yield
    except (SpecError, SpecErrors) as error:
        raise usage(spec_details(error, loc), error.complete) from None
    except NotInPanel as error:
        raise not_found(error.code, error.args[0], ["date"]) from None


def funnel_steps(strategy: Strategy, funnel: pl.DataFrame) -> list[FunnelStep]:
    """The funnel frame's steps of its one day, with their kinds, chain flags and group members."""
    steps, items = {step.name: step for step in strategy.steps()}, pool_items(strategy)
    found = []
    for name, passed, left in funnel.select("step", "passed", "left").iter_rows():
        step = steps.get(name)
        kind = "pool" if name in items else name if step is None else "condition" if step.expr is not None else "group"
        group = kind == "group"
        found.append(
            FunnelStep(
                step=name,
                kind=kind,
                chain=step.chain if step is not None else name not in ("targets", "backups"),
                passed=passed,
                left=left,
                mode=step.mode if group else None,
                members=list(step.members) if group else None,
            )
        )
    return found


def screen_rows(strategy: Strategy, section: Section, choice: Choice, panel: Panel, i: int = 0) -> list[ScreenRow]:
    """Decision row i's scored stocks by rank: role, weights, factor cells, formula and each comparison's value."""
    shares, pcts = weight_shares(strategy), percentiles(strategy, section)
    targets = np.flatnonzero(choice.target[i])  # the weights' columns, in order
    weights = dict(zip(targets.tolist(), zip(choice.weight.tolist(), choice.raw_weight.tolist())))
    rows = []
    for col in section.order[i, : section.scored[i].sum()].tolist():
        weight, raw = weights.get(col, (None, None))
        factors = [
            FactorCell(
                name=name,
                value=section.factors[name][i, col],
                pct=pcts[name][0][i, col],
                adjusted_pct=pcts[name][1][i, col],
                weight_share=share,
                contribution=share * pcts[name][1][i, col],
            )
            for name, share in shares.items()
        ]
        rows.append(
            ScreenRow(
                code=panel.codes[col],
                rank=section.rank[i, col],
                score=section.score[i, col],
                role="target" if weight is not None else "backup" if choice.backup[i, col] else "none",
                weight=weight,
                raw_weight=raw,
                capped=choice.capped[i, col],
                factors=factors,
                formula=None if section.formula is None else section.formula[i, col],
                values={name: _finite(value[i, col]) for name, value in section.values.items()},
            )
        )
    return rows


def reject_rows(strategy: Strategy, rejects: pl.DataFrame, section: Section, panel: Panel, i: int = 0) -> list[Reject]:
    """The rejects frame's rows with each simple comparison's op and thresholds and each group's member verdicts."""
    steps, column = {step.name: step for step in strategy.steps()}, {code: j for j, code in enumerate(panel.codes)}
    found = []
    for row in rejects.iter_rows(named=True):
        step = steps.get(row["step"])
        compared = comparison(step.expr) if step is not None and step.expr is not None else None
        members = None
        if row["kind"] == "group":
            cells = [section.steps[name][i, column[row["code"]]] for name in step.members]
            members = [MemberResult(name=name, passed=_verdict(v)) for name, v in zip(step.members, cells)]
        found.append(
            Reject(
                **row,
                op=compared and compared.op,
                threshold=compared and compared.threshold,
                threshold_hi=compared and compared.threshold_hi,
                members=members,
            )
        )
    return found


def _where(request: ScreenRequest) -> Loc:
    return ("strategy",) if request.strategy is not None else ("saved",)


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _verdict(value: int) -> bool | None:
    return None if value < 0 else bool(value == 1)
