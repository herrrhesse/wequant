"""The JSON layer of what a run is given: the strategy, the execution and the data options, mirroring the core types.

The core (wequant.strategy, wequant.account, wequant.backtest) is the only validator of meaning; these models only fix
the JSON types, the choices and single-field ranges, strictly, so no value is ever coerced.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, JsonValue, WrapValidator
from pydantic_core import PydanticCustomError

from wequant.account.execution import DELISTING, PRICES
from wequant.backtest.data import ANOMALY_MODES
from wequant.expr.vocab import INDEXES
from wequant.service.base import Code, Distinct, Model
from wequant.strategy.strategy import MODES, PERIODS, POOL_BOARDS, PREFERS, WEIGHTINGS


def _iso_date(value: object) -> object:
    if isinstance(value, dt.datetime):
        raise PydanticCustomError("wrong_type", "a date, not a date and time")
    if not isinstance(value, str):
        return value
    try:
        return dt.date.fromisoformat(value) if len(value) == 10 else value
    except ValueError:
        raise PydanticCustomError("wrong_type", "{value} is not a date YYYY-MM-DD", {"value": value}) from None


def _expr_form(value: object, handler: Callable) -> object:
    """A field name or a list; anything else is one not_a_form, not one error per union branch."""
    if not isinstance(value, str | list):
        raise PydanticCustomError("not_a_form", "an expression is a field name or [operator, arguments...]")
    return handler(value)


IsoDate = Annotated[dt.date, BeforeValidator(_iso_date)]  # a date object or "YYYY-MM-DD", never a timestamp
ExprForm = Annotated[str | list[JsonValue], WrapValidator(_expr_form)]  # a field name or [operator, arguments...]
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


class StocksSpec(Spec):
    """A fixed list of stocks: the strategy keeps its own copy, so a later change to the stock pool leaves it as it
    was."""

    codes: Annotated[list[Code], Distinct] = Field(min_length=1)
    """Codes such as 600000.SH, in the order shown."""
    pool_id: Name | None = None
    """The stock pool they were copied from (pools_list); shown only."""
    pool_name: Name | None = None
    """That pool's name when copied; shown only."""


class PoolSpec(Spec):
    """Explicit pool items, ANDed with the universe; all defaults leave the pool as the universe."""

    index: Literal[INDEXES] | None = None
    """Members of this index on the decision day; null: any. labels.scopes lists the ones offered; 000906.SH,
    000852.SH and 000985.SH are older options that existing strategies still read."""
    boards: list[Literal[POOL_BOARDS]] | None = Field(None, min_length=1)
    """Only these boards, no repeats; null: any board (the frontend writes null for all three)."""
    exclude_st: bool = False
    """Leave out stocks that are ST on the decision day (ST is unknown on the seed store)."""
    exclude_delisting: bool = False
    """剔除退市整理期股票: leave out a stock in its delisting period on the decision day (its final name carries 退:
    its last 15 trading days since 2020-12-31, 30 before). False when left out, so that saved strategies keep their
    results; new strategies and the templates set it."""
    min_list_days: int | None = Field(None, ge=2)
    """Leave out stocks listed for fewer trading days; null: no limit."""
    stocks: StocksSpec | None = None
    """Only the stocks on this fixed list (round 3); never together with index."""


def _filter(value: object, handler: Callable) -> object:
    """A group when it has conditions, else a condition: the errors of that one model, not of both."""
    if isinstance(value, dict):
        return (GroupSpec if "conditions" in value else ConditionSpec).model_validate(value)
    if isinstance(value, ConditionSpec | GroupSpec):
        return handler(value)
    raise PydanticCustomError("wrong_type", "a filter is an object: a condition, or a group with conditions")


Filter = Annotated[ConditionSpec | GroupSpec, WrapValidator(_filter)]


class StrategySpec(Spec):
    """What a rebalance strategy holds and when; defaults are left out of its canonical form.

    Cross-field rules (hold_rank > top_n, formula only with formula weighting, unique names) are checked by the core
    and reported with codes by strategy validate.
    """

    universe: ExprForm = ["NOT", "exchange@BJ"]
    """A condition expression: the scope, before the pool items."""
    filters: list[Filter] = []
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
