"""A finished account as a Run: benchmark column, metrics, decision frames, warnings and identities."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl

from wequant.account import EXECUTION_VERSION, Ledger
from wequant.analytics import METRICS_VERSION, compute_metrics
from wequant.backtest.backtest import Backtest
from wequant.backtest.warnings import run_warnings
from wequant.expr import EXPR_VERSION, panel_key
from wequant.panel import Panel
from wequant.runs.run import Run
from wequant.strategy import STRATEGY_VERSION, Section, detail_frame, funnel_frame, picks_frame


def versions(engine: str, version: int) -> dict[str, int]:
    """{engine: version} and the meaning versions of strategy, expr, execution and metrics."""
    return {
        engine: version,
        "strategy": STRATEGY_VERSION,
        "expr": EXPR_VERSION,
        "execution": EXECUTION_VERSION,
        "metrics": METRICS_VERSION,
    }


def benchmark_closes(backtest: Backtest, panel: Panel, base: int, last: int) -> np.ndarray | None:
    """(last - base + 1,) index closes of the run days, or None without a benchmark; ValueError when it has none."""
    if backtest.benchmark is None:
        return None
    closes = panel.index_close(backtest.benchmark)[base : last + 1]
    if np.isnan(closes).all():
        raise ValueError(f"benchmark: {backtest.benchmark} has no close on any run day")
    return closes


def assemble(
    backtest: Backtest,
    panel: Panel,
    ledger: Ledger,
    section: Section,
    closes: np.ndarray | None,
    engine: str,
    version: int,
) -> Run:
    """The Run of a closed account; engine and version name the engine in versions."""
    if closes is not None:
        ledger = replace(ledger, daily=ledger.daily.with_columns(pl.Series("benchmark", closes).fill_nan(None)))
    calendar = pl.Series("date", panel.dates)
    spec, strategy = backtest.metrics, backtest.strategy
    metrics = compute_metrics(ledger.daily, calendar, spec, positions=ledger.positions, orders=ledger.orders)
    return Run(
        engine,
        backtest.form,
        versions(engine, version),
        panel_key(panel),
        panel.dates[0].item(),
        ledger,
        metrics,
        spec,
        picks_frame(section, panel),
        funnel_frame(strategy, section, panel),
        detail_frame(strategy, section, panel),
        run_warnings(strategy, panel, section, ledger),
    )
