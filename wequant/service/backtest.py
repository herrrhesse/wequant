"""backtest: run a strategy with an execution block and data options, archive the run, report its metrics."""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import polars as pl
from pydantic import Field

from wequant.account.ledger import FORCED
from wequant.analytics import Metric
from wequant.backtest import Backtest, Cancelled, panel_for
from wequant.backtest import run as engine_run
from wequant.panel import NotInPanel
from wequant.runs.archive import RUNS, Archive
from wequant.service.base import Context, DataRequest, ErrorDetail, Model, manifest, not_found
from wequant.service.details import Loc, spec_details, usage
from wequant.service.errors import Exit, ServiceError
from wequant.service.outputs import (
    DataAnomaly,
    DelistedRow,
    MetricOut,
    OrderCounts,
    SavedRef,
    Warning,
    data_anomaly,
    warnings_out,
)
from wequant.service.specs import DataSpec, ExecutionSpec, IsoDate, MetricSpecModel
from wequant.service.strategies import Hash, StrategyInput, resolve, saved_as
from wequant.strategy import SpecError, SpecErrors, Strategy
from wequant.strategy.library import strategy_hash
from wequant.strategy.summary import sentence, summary
from wequant.version import code_version

STRATEGY_FIELDS = frozenset(f.name for f in dataclasses.fields(Strategy))  # a core error there is the strategy's


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
    """Trading days after the base day."""
    decisions: int
    """Decision days in the run."""
    metrics: dict[str, MetricOut]
    """In compute_metrics' order."""
    orders: OrderCounts
    delisted: list[DelistedRow]
    """Delisted holdings the run handled, by date."""
    anomalies: DataAnomaly | None
    warnings: list[Warning]
    report: str | None
    """Markdown; null unless asked for."""


def backtest(request: BacktestRequest, context: Context = Context()) -> BacktestResult:
    """Run the strategy with the execution block and data options, archive the run unless asked not to."""
    library, where = context.library(), _where(request)
    strategy, warnings = resolve(request, library)
    with located_run(where):
        spec = Backtest.from_form(_form(request, strategy))
    store = context.store()
    manifest(store, request.data_version)
    try:
        panel = panel_for(spec, store=store, version=request.data_version, cache_dir=context.cache)
    except NotInPanel as error:
        raise not_found(error.code, error.args[0], ["end"]) from None
    with located_run(where):
        run = engine_run(spec, panel, cache=context.expr_cache, progress=context.progress, cancel=context.cancel)
    if request.save:
        Archive(store.home / RUNS).save(run)
    found = strategy_hash(strategy)
    result = BacktestResult(
        run_id=run.id,
        saved=request.save,
        data_version=panel.version,
        strategy_hash=found,
        saved_as=saved_as(library, found),
        start=run.ledger.daily["date"][0],
        end=run.ledger.daily["date"][-1],
        run_days=run.ledger.daily.height - 1,
        decisions=run.funnel["date"].n_unique(),
        metrics=metrics_out(run.metrics),
        orders=order_counts(run.ledger.orders),
        delisted=[DelistedRow(**row) for row in run.ledger.delisted.iter_rows(named=True)],
        anomalies=data_anomaly(run.anomalies, panel.excluded),
        warnings=warnings + warnings_out(run.warnings),
        report=None,
    )
    return result.model_copy(update={"report": report(result, strategy)}) if request.report else result


@contextmanager
def located_run(where: Loc) -> Iterator[None]:
    """The core's refusal of a backtest as USAGE, a strategy field's under where; a cancelled run INTERRUPTED."""
    try:
        yield
    except (SpecError, SpecErrors) as error:
        raise usage([_relocated(d, where) for d in spec_details(error)], error.complete) from None
    except Cancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None


def metrics_out(metrics: Mapping[str, Metric]) -> dict[str, MetricOut]:
    """Each metric as a MetricOut, in its order."""
    return {name: MetricOut(value=m.value, unit=m.unit, reason=m.reason) for name, m in metrics.items()}


def order_counts(orders: pl.DataFrame) -> OrderCounts:
    """Rebalance orders by status and rejection reason; forced delisting rows counted apart."""
    forced = orders["reason"].is_in(FORCED).fill_null(False)
    trades, statuses = orders.filter(~forced), orders.filter(~forced)["status"]
    rejected = trades.filter(pl.col("status") == "rejected")["reason"]
    return OrderCounts(
        total=trades.height,
        filled=int((statuses == "filled").sum()),
        partial=int((statuses == "partial").sum()),
        rejected=rejected.len(),
        rejected_by_reason=_counts(rejected),
        forced=_counts(orders.filter(forced)["reason"]),
    )


def report(result: BacktestResult, strategy: Strategy) -> str:
    """Markdown: the run's identity and strategy, headline metrics, orders, delisted holdings and warnings."""
    saved = ", ".join(f"{ref.id}@{ref.n}" for ref in result.saved_as) or "unsaved"
    lines = [
        f"# Backtest {result.run_id}",
        "",
        f"- Store v{result.data_version}, code {code_version()}, {result.start} to {result.end}"
        f" ({result.run_days} run days, {result.decisions} decisions)",
        f"- Strategy {result.strategy_hash} ({saved}): {sentence(summary(strategy))}",
        "",
        "## Metrics",
        "",
        "| metric | value | unit |",
        "| --- | --- | --- |",
        *(f"| {name} | {_cell(m)} | {m.unit} |" for name, m in result.metrics.items()),
        "",
        "## Orders",
        "",
        f"{result.orders.total} orders: {result.orders.filled} filled, {result.orders.partial} partial,"
        f" {result.orders.rejected} rejected {result.orders.rejected_by_reason or ''}".rstrip(),
        *([f"Forced at delisting: {result.orders.forced}"] if result.orders.forced else []),
        *(
            f"- {row.date} {row.code}: {row.treatment}, {row.value:,.2f} yuan at the last close, {row.cash:,.2f} back"
            for row in result.delisted
        ),
        "",
        "## Warnings",
        "",
        *([f"- {w.code}: {w.text}" for w in result.warnings] or ["None."]),
    ]
    return "\n".join(lines) + "\n"


def _form(request: BacktestRequest, strategy: Strategy) -> dict:
    """The core backtest form of the request."""
    dates = {name: day and day.isoformat() for name, day in (("start", request.start), ("end", request.end))}
    specs = {name: getattr(request, name).model_dump() for name in ("execution", "metrics", "data")}
    return {"strategy": strategy.form, **dates, "cash": request.cash, "benchmark": request.benchmark, **specs}


def _relocated(detail: ErrorDetail, where: Loc) -> ErrorDetail:
    """detail with a location in the backtest's strategy moved under where (the request's strategy or saved)."""
    loc = detail.loc
    if loc[:1] == ["strategy"]:
        loc = [*where, *loc[1:]]
    elif loc and loc[0] in STRATEGY_FIELDS:
        loc = [*where, *loc]
    return detail.model_copy(update={"loc": loc})


def _where(request: BacktestRequest) -> Loc:
    return ("strategy",) if request.strategy is not None else ("saved",)


def _counts(values: pl.Series) -> dict[str, int]:
    return {str(k): int(n) for k, n in sorted(values.value_counts().iter_rows())}


def _cell(metric: MetricOut) -> str:
    if metric.value is None:
        return f"- ({metric.reason})"
    return f"{metric.value:.4f}" if isinstance(metric.value, float) else str(metric.value)
