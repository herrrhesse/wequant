"""The JSON layer of what a run is given: the strategy, the execution and the data options, mirroring the core types.

The core (wequant.strategy, wequant.account, wequant.backtest) is the only validator of meaning; these models only fix
the JSON types, the choices and single-field ranges, strictly, so no value is ever coerced.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, JsonValue

from wequant.account.execution import DELISTING, PRICES
from wequant.backtest.data import ANOMALY_MODES
from wequant.expr.vocab import INDEXES
from wequant.service.base import Model
from wequant.strategy.strategy import MODES, PERIODS, POOL_BOARDS, PREFERS, WEIGHTINGS


def _iso_date(value: object) -> object:
    if isinstance(value, str) and len(value) == 10:
        return dt.date.fromisoformat(value)
    if isinstance(value, dt.datetime):
        raise ValueError("a date, not a date and time")
    return value


IsoDate = Annotated[dt.date, BeforeValidator(_iso_date)]  # a date object or "YYYY-MM-DD", never a timestamp
ExprForm = str | list[JsonValue]  # a canonical expression form: a field name or [operator, arguments...]
Name = Annotated[str, Field(min_length=1)]


class Spec(Model):
    """Base of the given inputs: strict JSON types on top of Model's rules."""

    model_config = ConfigDict(strict=True)


class ConditionSpec(Spec):
    """A condition every stock must pass at the decision day's close."""

    name: Name
    expr: ExprForm
    """A condition (bool) expression; cross-sectional operators rank within the pool."""
    enabled: bool = True


class GroupSpec(Spec):
    """Conditions combined: all of them, or any of them."""

    name: Name
    conditions: list[ConditionSpec] = Field(min_length=1)
    mode: Literal[MODES] = "all"
    enabled: bool = True


class FactorSpec(Spec):
    """A scoring factor: ranked as a percentile among the scored stocks, weighted."""

    name: Name
    expr: ExprForm
    prefer: Literal[PREFERS] = "high"
    weight: float = Field(1.0, gt=0)
    """Relative weight; weights are normalised over the enabled factors."""
    enabled: bool = True


class PoolSpec(Spec):
    """Explicit pool items, ANDed with the universe; all defaults leave the pool as the universe."""

    index: Literal[INDEXES] | None = None
    """Members of this index on the decision day; null: any."""
    boards: list[Literal[POOL_BOARDS]] | None = Field(None, min_length=1)
    """Only these boards, no repeats; null: any board (the frontend writes null for all three)."""
    exclude_st: bool = False
    """Leave out stocks that are ST on the decision day (ST is unknown on the seed store)."""
    min_list_days: int | None = Field(None, ge=2)
    """Leave out stocks listed for fewer trading days; null: no limit."""


class StrategySpec(Spec):
    """What a rebalance strategy holds and when; defaults are left out of its canonical form.

    Cross-field rules (hold_rank > top_n, formula only with formula weighting, unique names) are checked by the core
    and reported with codes by strategy validate.
    """

    universe: ExprForm = ["NOT", "exchange@BJ"]
    """A condition expression: the scope, before the pool items."""
    filters: list[ConditionSpec | GroupSpec] = []
    factors: list[FactorSpec] = []
    top_n: int | None = Field(None, ge=1)
    """Number of targets; null: every scored stock."""
    backups: int = Field(0, ge=0)
    period: Literal[PERIODS] = "week"
    every: int = Field(1, ge=1)
    """Rebalance every this many periods."""
    weighting: Literal[WEIGHTINGS] = "equal"
    formula: ExprForm | None = None
    """The weight formula, with formula weighting only."""
    band: float | None = Field(None, ge=0)
    """Resize a kept target only when its weight is off by more than this share of its target; null: never."""
    anchor: IsoDate | None = None
    """The trading day expressions count from; null: the calendar's first day."""
    hold_rank: int | None = Field(None, ge=1)
    """Keep a holding while it ranks within this many (> top_n); null: no buffer."""
    max_weight: float | None = Field(None, gt=0, lt=1)
    """Cap on one target's weight; the excess goes to the other targets, cash only when none can take it."""
    pool: PoolSpec = PoolSpec()


class ExecutionSpec(Spec):
    """How orders fill and what a run pays; outside the strategy, part of the run's inputs."""

    price: Literal[PRICES] = "open"
    """The fill day's raw open (or close)."""
    commission_rate: float = Field(0.0003, ge=0, lt=1)
    min_commission: float = Field(5.0, ge=0)
    """Yuan per order, a whole number of fen."""
    slippage_bps: float = Field(10.0, ge=0, lt=10000)
    """Per side, a cost on the trade value; never moves the fill price or the limit checks."""
    delisting: Literal[DELISTING] = "auto"
    """auto: write down forced delistings, cash mergers at the last close, hold long suspensions; hold: as before."""
    failure_recovery: float = Field(0.0, ge=0, le=1)
    """Share of its last close a written-down holding returns as cash, for sensitivity checks."""


class DataSpec(Spec):
    """How a run treats anomalous data; outside the strategy, part of the run's inputs.

    Excluding is a robustness check, not a point-in-time rule: anomaly labels may rest on the next day's data.
    """

    anomalies: Literal[ANOMALY_MODES] = "flag"
    """flag: use and report; exclude_day: read error-level anomalous stock-days as missing."""
    exclude_suspicious: bool = False
    """Also exclude suspicious-level days (exclude_day only)."""


class MetricSpecModel(Spec):
    """The metric conventions of a run."""

    periods_per_year: int = Field(242, ge=1)
    risk_free_rate: float = 0.0
    tail_fraction: float = Field(0.05, gt=0, lt=1)


class StrategyRef(Spec):
    """A saved strategy version."""

    id: Name
    n: int | None = Field(None, ge=1)
    """The version; null: the current one."""
