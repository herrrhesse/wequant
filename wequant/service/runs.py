"""runs list | show | rerun | diff | orders | day | anomalies: the run archive."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import ConfigDict, Field

from wequant.backtest.anomalies import CHANNELS
from wequant.service.base import Model
from wequant.service.outputs import (
    Action,
    AnomalyUse,
    DataAnomaly,
    DelistedRow,
    MetricOut,
    OrderCounts,
    OrderRow,
    Role,
    SavedRef,
    SellReason,
    Warning,
)
from wequant.service.specs import DataSpec, ExecutionSpec, IsoDate, MetricSpecModel, Name, StrategySpec
from wequant.service.strategies import Hash

Limit = Field(100, ge=1, le=500)


class Page(Model):
    """One page of a list without an upper bound."""

    total: int
    offset: int
    limit: int


class PageRequest(Model):
    """Paging of a list without an upper bound."""

    limit: int = Limit
    offset: int = Field(0, ge=0)


class RunsListRequest(PageRequest):
    """Archived runs, newest first."""

    strategy_hash: Hash | None = None
    """Only runs of this strategy content; null: all."""


class RunRow(Model):
    """One archived run."""

    id: str
    engine: str
    created_at: str
    code_version: str
    store_version: int
    anchor: dt.date
    start: dt.date
    end: dt.date
    strategy_hash: Hash
    saved_as: list[SavedRef]
    total_return: float | None
    annual_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    rerunnable: bool
    """Its store version is still kept."""


class RunsPage(Page):
    """Archived runs, newest first."""

    rows: list[RunRow]


class RunRequest(Model):
    """One archived run."""

    id: Name


class FeeRule(Model):
    """A stamp-tax or transfer-fee rule in force during the run, for display."""

    table: Literal["stamp_tax", "transfer_fee"]
    id: str
    since: dt.date
    until: dt.date | None
    """The day before the next rule of its table; null: in force to the run's end."""
    exchanges: list[str] | None
    """null: both exchanges."""
    buy_rate: float
    sell_rate: float
    basis: Literal["amount", "par"] | None
    """Transfer fee on the trade value or on par value; null: stamp tax."""
    note: str | None
    """The rule's confirm note; null: primary source known."""


class DailyRow(Model):
    """One day of a run, the base day first; the derived columns use wequant.analytics' definitions."""

    date: dt.date
    equity: float
    cash: float
    bought: float
    sold: float
    """Yuan, delisting cash included."""
    benchmark: float | None
    """Index close; null: no benchmark or no close that day."""
    positions: int
    invested: float
    """Yuan of holdings at the close."""
    nav: float
    """equity / initial cash, 1 on the base day."""
    benchmark_nav: float | None
    """benchmark / its base-day close; null: no benchmark or no close."""
    excess: float | None
    """nav / benchmark_nav - 1; null with benchmark_nav."""
    drawdown: float
    """1 - nav / its running maximum."""
    exposure: float
    """invested / equity."""


class PeriodRow(Model):
    """One year or month of a run, not annualised."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    period: str
    """"2024" or "2024-03"."""
    return_: float = Field(alias="return")
    benchmark_return: float | None
    """null: no benchmark, or a missing close in the period."""
    excess: float | None
    """(1 + return) / (1 + benchmark_return) - 1."""
    max_drawdown: float
    """Within the period, from the previous period's last nav."""


class Periods(Model):
    """Returns by year and by month."""

    year: list[PeriodRow]
    month: list[PeriodRow]


class RunDetail(RunRow):
    """An archived run with its inputs, results and derived daily and period views."""

    strategy: StrategySpec
    execution: ExecutionSpec
    data: DataSpec
    cash: float
    benchmark: str | None
    metrics_spec: MetricSpecModel
    versions: dict[str, int]
    metrics: dict[str, MetricOut]
    orders: OrderCounts
    delisted: list[DelistedRow]
    anomalies: DataAnomaly | None
    warnings: list[Warning]
    fees: list[FeeRule]
    daily: list[DailyRow]
    periods: Periods


class RunComparison(Model):
    """A re-run compared byte for byte with the archive."""

    id: str
    identical: bool
    differences: dict[str, str]
    """By file: what differs."""
    notes: list[str]
    """e.g. a different code version."""


class RunDiffRequest(Model):
    """Two archived runs."""

    a: Name
    b: Name


class MetricDiff(Model):
    """A metric that differs between two runs."""

    name: str
    unit: str
    a: float | int | dt.date | None
    b: float | int | dt.date | None


class RunDiff(Model):
    """How two runs' inputs and metrics differ."""

    a: str
    b: str
    inputs: list[str]
    """One line per differing input: "path: old -> new"."""
    metrics: list[MetricDiff]


class OrdersRequest(PageRequest):
    """A run's orders, filtered."""

    id: Name
    code: str | None = None
    status: Literal["filled", "partial", "rejected"] | None = None
    side: Literal["buy", "sell"] | None = None
    start: IsoDate | None = None
    end: IsoDate | None = None


class OrdersPage(Page):
    """Orders in archive order: date, batch, submission."""

    rows: list[OrderRow]


class RunDayRequest(Model):
    """One day of a run."""

    id: Name
    date: IsoDate


class PositionRow(Model):
    """A holding at a day's close."""

    code: str
    shares: float
    value: float
    weight: float
    """value / equity."""


class DecisionRow(Model):
    """A target, backup or held stock on a decision day."""

    code: str
    role: Role
    rank: int | None
    """null: no score."""
    score: float | None
    weight: float | None
    """null: not a target."""
    raw_weight: float | None
    held: bool
    action: Action
    sell_reason: SellReason | None
    """sell only."""


class RunDay(Model):
    """A day's holdings, decisions and orders."""

    id: str
    date: dt.date
    equity: float
    cash: float
    positions: list[PositionRow]
    """By value, largest first."""
    decision_day: bool
    decisions: list[DecisionRow]
    """Empty when not a decision day."""
    orders: list[OrderRow]
    """Filled, rejected and forced, in archive order."""


class AnomaliesRequest(PageRequest):
    """A run's uses of anomalous data, filtered."""

    id: Name
    channel: Literal[CHANNELS] | None = None
    code: str | None = None


class AnomaliesPage(Page):
    """Uses in archive order (date, code, channel, anomaly_date, kind, seq)."""

    rows: list[AnomalyUse]
