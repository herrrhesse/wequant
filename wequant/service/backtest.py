"""backtest: run a strategy with an execution block and data options, archive the run, report its metrics."""

from __future__ import annotations

import datetime as dt

from pydantic import Field

from wequant.service.base import DataRequest, Model
from wequant.service.outputs import DataAnomaly, DelistedRow, MetricOut, OrderCounts, SavedRef, Warning
from wequant.service.specs import DataSpec, ExecutionSpec, IsoDate, MetricSpecModel
from wequant.service.strategies import Hash, StrategyInput


class BacktestRequest(StrategyInput, DataRequest):
    """A strategy (inline or saved) over a date range."""

    start: IsoDate | None = None
    """Base day: the first trading day on or after it; null: the panel's first day."""
    end: IsoDate | None = None
    """Last day: the last trading day on or before it; null: the panel's last day."""
    cash: float = Field(1_000_000.0, gt=0)
    """Yuan, a whole number of fen."""
    execution: ExecutionSpec = ExecutionSpec()
    data: DataSpec = DataSpec()
    benchmark: str | None = "000300.SH"
    """An index_bars code; null: no benchmark."""
    metrics: MetricSpecModel = MetricSpecModel()
    save: bool = True
    """Archive the run."""
    report: bool = False
    """Attach a Markdown report."""


class BacktestResult(Model):
    """A finished run's identity, metrics, orders, delisted holdings, anomalies and warnings."""

    run_id: str
    saved: bool
    """Archived (also when an identical run was already)."""
    data_version: int
    strategy_hash: Hash
    saved_as: list[SavedRef]
    start: dt.date
    end: dt.date
    run_days: int
    decisions: int
    metrics: dict[str, MetricOut]
    """In compute_metrics' order."""
    orders: OrderCounts
    delisted: list[DelistedRow]
    """Delisted holdings the run handled, by date."""
    anomalies: DataAnomaly | None
    warnings: list[Warning]
    report: str | None
    """Markdown; null unless asked for."""
