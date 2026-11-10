"""explain: one stock on one day, from the same cross-section as screen and, with a run, the archived decisions."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from wequant.service.base import Code, Model
from wequant.service.outputs import Action, AnomalyUse, Op, OrderRow, Role, SellReason, Warning
from wequant.service.screen import FactorCell, MemberResult
from wequant.service.specs import DataSpec, IsoDate, Name, StrategyRef, StrategySpec
from wequant.service.strategies import Hash


class ExplainRequest(Model):
    """A stock and a day, for a strategy (inline or saved) or an archived run: exactly one of the three."""

    strategy: StrategySpec | None = None
    saved: StrategyRef | None = None
    run: Name | None = None
    code: Code
    date: IsoDate
    """The decision day; with a run also a fill day."""
    data: DataSpec | None = None
    """null: flag without a run, the run's options with one (then it must be null)."""
    data_version: int | None = None
    """null: the current version, or the run's (then it must be null)."""

    @model_validator(mode="after")
    def _exactly_one(self) -> ExplainRequest:
        if sum(x is not None for x in (self.strategy, self.saved, self.run)) != 1:
            raise PydanticCustomError("requires", "give exactly one of strategy, saved and run")
        return self


class PoolCheck(Model):
    """One pool step: bar, universe, then the set pool items."""

    name: str
    passed: bool | None
    """null: no value, which fails too."""
    value: float | bool | str | None
    """bar: t's close; index: membership; boards: the stock's board; exclude_st: its ST flag; min_list_days: list days."""
    note: str | None
    """e.g. listing days counted from the data's first day before S2.3."""


class StepCheck(Model):
    """One filter step, in funnel order."""

    name: str
    kind: Literal["condition", "group"]
    chain: bool
    passed: bool | None
    value: float | None
    op: Op | None
    threshold: float | None
    threshold_hi: float | None
    text: str
    """The expression's canonical form as written."""
    mode: Literal["all", "any"] | None
    members: list[MemberResult] | None


class FactorScore(FactorCell):
    """A factor with its definition."""

    prefer: Literal["high", "low"]
    weight: float


class ScoreCheck(Model):
    """The stock's score among the scored stocks."""

    scored_count: int
    score: float
    rank: int
    factors: list[FactorScore]


class Explain(Model):
    """Why a stock was or was not chosen on a day, and with a run what happened to it."""

    data_version: int
    date: dt.date
    next_day: dt.date | None
    code: str
    strategy_hash: Hash
    run_id: str | None
    pool: list[PoolCheck]
    steps: list[StepCheck]
    score: ScoreCheck | None
    """null: not scored."""
    missing: str | None
    """Not scored for want of this factor (or formula); null otherwise."""
    role: Role
    weight: float | None
    raw_weight: float | None
    held: bool | None
    """null without a run."""
    action: Action | None
    sell_reason: SellReason | None
    execution: list[OrderRow] | None
    """t+1's orders of this stock, forced exits included; null without a run."""
    anomalies: list[AnomalyUse]
    warnings: list[Warning]
