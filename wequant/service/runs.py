"""runs list | show | rerun | diff | orders | day | anomalies: the run archive."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Literal

import polars as pl
from pydantic import ConfigDict, Field

from wequant.analytics import daily_view, period_view
from wequant.backtest import Backtest, Cancelled, run_form
from wequant.backtest.anomalies import CHANNELS
from wequant.canonical import digest
from wequant.data.store import Store
from wequant.rules.book import Rate, default_book, fees_in_force
from wequant.runs.archive import HEADLINE, RUNS, Archive
from wequant.runs.compare import json_differences
from wequant.runs.run import Run
from wequant.service.backtest import BacktestResult, metrics_out, order_counts, report
from wequant.service.base import Context, Model, name_of, not_found
from wequant.service.errors import Exit, ServiceError
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
    anomaly_use,
    data_anomaly,
    order_row,
    warnings_out,
)
from wequant.service.specs import DataSpec, ExecutionSpec, IsoDate, MetricSpecModel, Name, StrategySpec
from wequant.service.strategies import Hash, saved_as
from wequant.strategy.library import Library, strategy_hash

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
    data_version_name: str | None
    """The store version's display name, YYYYMMDD.N; null once that version is no longer kept."""
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
    """null: not a target or backup (the archive keeps the picks' scores), or no score."""
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


def runs_list(request: RunsListRequest, context: Context = Context()) -> RunsPage:
    """Archived runs, newest first, optionally of one strategy content."""
    store = context.store()
    index = Archive(store.home / RUNS).index().sort("created_at", "id", descending=[True, False])
    refs, names = saved_refs(context.library()), kept_names(store)
    rows = [_run_row(row, refs, names) for row in index.iter_rows(named=True)]
    if request.strategy_hash is not None:
        rows = [row for row in rows if row.strategy_hash == request.strategy_hash]
    window = rows[request.offset : request.offset + request.limit]
    return RunsPage(total=len(rows), offset=request.offset, limit=request.limit, rows=window)


def runs_show(request: RunRequest, context: Context = Context()) -> RunDetail:
    """One archived run: its inputs, metrics, orders, delisted holdings, anomalies, warnings, fee rules, daily rows
    and periods."""
    archive, run = load_run(context, request.id)
    store = context.store()
    row = _run_row(archive.row(request.id), saved_refs(context.library()), kept_names(store))
    spec = Backtest.from_form(run.description)
    start, end = run.ledger.daily["date"][0], run.ledger.daily["date"][-1]
    return RunDetail(
        **row.model_dump(),
        strategy=StrategySpec.model_validate(spec.strategy.form),
        execution=ExecutionSpec.model_validate(spec.execution.to_dict()),
        data=DataSpec.model_validate(spec.data.to_dict()),
        cash=spec.cash,
        benchmark=spec.benchmark,
        metrics_spec=MetricSpecModel.model_validate(dataclasses.asdict(spec.metrics)),
        versions=run.versions,
        metrics=metrics_out(run.metrics),
        orders=order_counts(run.ledger.orders),
        delisted=[DelistedRow(**r) for r in run.ledger.delisted.iter_rows(named=True)],
        anomalies=data_anomaly(run.anomalies, spec.data.excluded),
        warnings=warnings_out(run.warnings),
        fees=[_fee(table, rule, until) for table, rule, until in fees_in_force(default_book(), start, end)],
        daily=[DailyRow(**r) for r in daily_view(run.ledger.daily, run.ledger.positions).iter_rows(named=True)],
        periods=Periods(**{p: _periods(run.ledger.daily, p) for p in ("year", "month")}),
    )


class RunReport(Model):
    """An archived run's Markdown report."""

    id: str
    report: str
    """The text backtest attaches with report true, from the archive."""


def runs_report(request: RunRequest, context: Context = Context()) -> RunReport:
    """The Markdown report of an archived run, as backtest attaches it with report true (F-28)."""
    archive, run = load_run(context, request.id)
    row = archive.row(request.id)
    spec = Backtest.from_form(run.description)
    found = strategy_hash(spec.strategy)
    result = BacktestResult(
        run_id=run.id,
        saved=True,
        data_version=row["store_version"],
        data_version_name=name_of(context.store(), row["store_version"]) or f"v{row['store_version']}",
        strategy_hash=found,
        saved_as=saved_as(context.library(), found),
        start=run.ledger.daily["date"][0],
        end=run.ledger.daily["date"][-1],
        run_days=run.ledger.daily.height - 1,
        decisions=run.funnel["date"].n_unique(),
        metrics=metrics_out(run.metrics),
        orders=order_counts(run.ledger.orders),
        delisted=[DelistedRow(**r) for r in run.ledger.delisted.iter_rows(named=True)],
        anomalies=data_anomaly(run.anomalies, spec.data.excluded),
        warnings=warnings_out(run.warnings),
        report=None,
    )
    return RunReport(id=run.id, report=report(result, spec.strategy, row["code_version"]))


def runs_rerun(request: RunRequest, context: Context = Context()) -> RunComparison:
    """Run an archived run again on its store version and compare the bytes."""
    archive, run = load_run(context, request.id)
    store = context.store()
    if run.store_version not in store.versions():
        message = f"run {request.id} read store version {run.store_version}, which is no longer kept"
        raise not_found("version_not_kept", message, ["id"])
    hooks = {"cache": context.expr_cache, "progress": context.progress, "cancel": context.cancel}
    try:
        found = archive.rerun(
            request.id, lambda form, version: run_form(form, version, store=store, cache_dir=context.cache, **hooks)
        )
    except Cancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None
    return RunComparison(
        id=request.id, identical=found.identical, differences=found.differences, notes=list(found.notes)
    )


def runs_diff(request: RunDiffRequest, context: Context = Context()) -> RunDiff:
    """How two archived runs' inputs and metrics differ."""
    (_, a), (_, b) = load_run(context, request.a, ["a"]), load_run(context, request.b, ["b"])
    names = [*a.metrics, *(name for name in b.metrics if name not in a.metrics)]
    metrics = [
        MetricDiff(name=name, unit=(a.metrics.get(name) or b.metrics[name]).unit, a=_value(a, name), b=_value(b, name))
        for name in names
        if _value(a, name) != _value(b, name)
    ]
    return RunDiff(a=request.a, b=request.b, inputs=json_differences(a.inputs, b.inputs), metrics=metrics)


def runs_orders(request: OrdersRequest, context: Context = Context()) -> OrdersPage:
    """A run's orders in archive order, filtered and paged."""
    _, run = load_run(context, request.id)
    orders = run.ledger.orders.filter(
        *[pl.col(name) == getattr(request, name) for name in ("code", "status", "side") if getattr(request, name)],
        *([pl.col("date") >= request.start] if request.start else []),
        *([pl.col("date") <= request.end] if request.end else []),
    )
    window = orders.slice(request.offset, request.limit)
    rows = [order_row(row) for row in window.iter_rows(named=True)]
    return OrdersPage(total=orders.height, offset=request.offset, limit=request.limit, rows=rows)


def runs_day(request: RunDayRequest, context: Context = Context()) -> RunDay:
    """One day of a run: its holdings at the close, the decisions taken at that close and the orders filled that day."""
    _, run = load_run(context, request.id)
    day = run.ledger.daily.filter(pl.col("date") == request.date)
    if day.is_empty():
        raise not_found("not_a_trading_day", f"{request.date} is not a day of run {request.id}", ["date"])
    equity = day["equity"].item()
    held = run.ledger.positions.filter(pl.col("date") == request.date).sort("value", "code", descending=[True, False])
    positions = [
        PositionRow(code=r["code"], shares=r["shares"], value=r["value"], weight=r["value"] / equity)
        for r in held.iter_rows(named=True)
    ]
    orders = run.ledger.orders.filter(pl.col("date") == request.date)
    return RunDay(
        id=request.id,
        date=request.date,
        equity=equity,
        cash=day["cash"].item(),
        positions=positions,
        decision_day=bool((run.funnel["date"] == request.date).any()),
        decisions=_decisions(run, request.date),
        orders=[order_row(row) for row in orders.iter_rows(named=True)],
    )


def runs_anomalies(request: AnomaliesRequest, context: Context = Context()) -> AnomaliesPage:
    """A run's uses of anomalous data in archive order, filtered and paged."""
    _, run = load_run(context, request.id)
    uses = run.anomalies.filter(
        *([pl.col("channel").cast(pl.String) == request.channel] if request.channel else []),
        *([pl.col("code") == request.code] if request.code else []),
    )
    rows = [anomaly_use(row) for row in uses.slice(request.offset, request.limit).iter_rows(named=True)]
    return AnomaliesPage(total=uses.height, offset=request.offset, limit=request.limit, rows=rows)


def saved_refs(library: Library) -> dict[str, list[SavedRef]]:
    """Every saved version by its strategy hash."""
    found: dict[str, list[SavedRef]] = {}
    for entry in library.entries():
        for version in entry.versions:
            found.setdefault(version.strategy_hash, []).append(SavedRef(id=entry.id, n=version.n))
    return found


def load_run(context: Context, run_id: str, loc: list = ("id",)) -> tuple[Archive, Run]:
    """The archive and its run run_id; NOT_FOUND unknown_run at loc when there is none."""
    archive = Archive(context.store().home / RUNS)
    if run_id not in archive:
        raise not_found("unknown_run", f"no run {run_id} in {archive.root}", list(loc))
    return archive, archive.load(run_id)


def kept_names(store: Store) -> dict[int, str]:
    """version -> display name of every version the store keeps."""
    return {v: name for v in store.versions() if (name := name_of(store, v)) is not None}


def _run_row(row: dict, refs: dict[str, list[SavedRef]], names: dict[int, str]) -> RunRow:
    """The RunRow of an archive index row; names are the kept versions' display names."""
    found = digest(json.loads(row["description"])["strategy"])
    headline = {name: row[name] for name in HEADLINE}
    fields = ("id", "engine", "created_at", "code_version", "store_version", "anchor", "start", "end")
    return RunRow(
        **{name: row[name] for name in fields},
        **headline,
        data_version_name=names.get(row["store_version"]),
        strategy_hash=found,
        saved_as=refs.get(found, []),
        rerunnable=row["store_version"] in names,
    )


def _fee(table: str, rule: Rate, until: dt.date | None) -> FeeRule:
    stamp = table == "stamp_tax"
    return FeeRule(
        table=table,
        id=rule.id,
        since=rule.since,
        until=until,
        exchanges=None if stamp else list(rule.exchanges),
        buy_rate=float(rule.buy),
        sell_rate=float(rule.sell),
        basis=None if stamp else rule.basis,
        note=rule.confirm,
    )


def _periods(daily: pl.DataFrame, period: str) -> list[PeriodRow]:
    return [PeriodRow.model_validate(row) for row in period_view(daily, period).iter_rows(named=True)]


def _value(run: Run, name: str) -> float | int | dt.date | None:
    metric = run.metrics.get(name)
    return None if metric is None else metric.value


def _decisions(run: Run, day: dt.date) -> list[DecisionRow]:
    """The day's targets, backups and held stocks: by rank, the unranked after by code."""
    picks = run.picks.filter(pl.col("date") == day).select("code", "role", "score", "weight", "raw_weight")
    detail = run.detail.filter(pl.col("date") == day).join(picks, on="code", how="left")
    chosen = detail.filter(pl.col("role").is_not_null() | pl.col("held")).sort("rank", "code", nulls_last=True)
    rows = []
    for r in chosen.iter_rows(named=True):
        sold = r["action"] == "sell"
        reason = sold and SellReason(
            kind=r["sell_kind"], step=r["sell_step"], rank=r["sell_rank"], limit=r["sell_limit"]
        )
        rows.append(
            DecisionRow(
                code=r["code"],
                role=r["role"] or "none",
                rank=r["rank"],
                score=r["score"],
                weight=r["weight"],
                raw_weight=r["raw_weight"],
                held=r["held"],
                action=r["action"],
                sell_reason=reason if sold else None,
            )
        )
    return rows
