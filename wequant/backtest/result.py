"""A finished account as a Run: benchmark column, metrics, decision frames, warnings and identities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import polars as pl

from wequant.account import EXECUTION_VERSION, Ledger
from wequant.account.ledger import rebalance_orders
from wequant.analytics import METRICS_VERSION, compute_metrics
from wequant.backtest.anomalies import run_uses
from wequant.backtest.backtest import Backtest
from wequant.backtest.warnings import run_warnings
from wequant.data.schema import ANOMALY_VERSION
from wequant.expr import EXPR_VERSION, panel_key
from wequant.panel import Panel
from wequant.runs.run import Run
from wequant.strategy import STRATEGY_VERSION, Choice, Section, SpecError, detail_frame, funnel_frame, picks_frame


def versions(engine: str, version: int) -> dict[str, int]:
    """{engine: version} and the meaning versions of strategy, expr, execution, metrics and the anomaly classes."""
    return {
        engine: version,
        "strategy": STRATEGY_VERSION,
        "expr": EXPR_VERSION,
        "execution": EXECUTION_VERSION,
        "metrics": METRICS_VERSION,
        "anomaly": ANOMALY_VERSION,
    }


def benchmark_closes(backtest: Backtest, panel: Panel, base: int, last: int) -> np.ndarray | None:
    """(last - base + 1,) index closes of the run days, or None without a benchmark; SpecError when it has none."""
    if backtest.benchmark is None:
        return None
    closes = panel.index_close(backtest.benchmark)[base : last + 1]
    if np.isnan(closes).all():
        raise SpecError("no_data", ("benchmark",), f"{backtest.benchmark} has no close on any run day")
    return closes


def assemble(
    backtest: Backtest,
    panel: Panel,
    ledger: Ledger,
    section: Section,
    choice: Choice,
    closes: np.ndarray | None,
    engine: str,
    version: int,
    mark: Callable[[str], None] | None = None,
) -> Run:
    """The Run of a closed account and the stacked choices of its decision rows; engine and version name the engine in
    versions; mark(stage) as each stage ends: anomalies, metrics, frames."""
    mark = mark or (lambda stage: None)
    if closes is not None:
        ledger = replace(ledger, daily=ledger.daily.with_columns(pl.Series("benchmark", closes).fill_nan(None)))
    calendar = pl.Series("date", panel.dates)
    spec, strategy = backtest.metrics, backtest.strategy
    uses = run_uses(strategy, panel, section, choice, ledger)
    mark("anomalies")
    orders = rebalance_orders(ledger.orders)
    metrics = compute_metrics(ledger.daily, calendar, spec, positions=ledger.positions, orders=orders)
    mark("metrics")
    frames = (
        picks_frame(section, choice, panel),
        funnel_frame(strategy, section, choice, panel),
        detail_frame(strategy, section, choice, panel),
    )
    warnings = run_warnings(strategy, panel, section, choice, ledger, uses)
    mark("frames")
    meta = (engine, backtest.form, versions(engine, version), panel_key(panel), panel.dates[0].item())
    return Run(*meta, ledger, metrics, spec, *frames, uses, warnings)
