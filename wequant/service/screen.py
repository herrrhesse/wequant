"""screen: one decision day's cross-section of a strategy, the backtest's own path with no holdings."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from wequant.service.base import DataRequest, Model
from wequant.service.outputs import DataAnomaly, Op, Role, SavedRef, Warning
from wequant.service.specs import DataSpec, IsoDate
from wequant.service.strategies import Hash, StrategyInput

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
