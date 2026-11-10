"""The rebalance engine: decide at each decision day's close, trade the next day by the batch protocol, hold between."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from wequant.account import Account, Target, trade_target
from wequant.backtest.backtest import Backtest, panel_for, span
from wequant.backtest.progress import PROGRESS_DAYS, Progress, check_cancel, checkpoint
from wequant.backtest.result import assemble, benchmark_closes, versions
from wequant.expr import Cache, panel_key
from wequant.panel import Panel
from wequant.runs.run import Run, run_id, run_inputs
from wequant.strategy import Choice, Section, choose, decision_rows, select, stack

if TYPE_CHECKING:
    from wequant.data.store import Store

ENGINE = "rebalance"
REBALANCE_VERSION = 1  # bump when how decisions become orders changes meaning


def run(
    backtest: Backtest,
    panel: Panel,
    *,
    cache: Cache | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Run:
    """The backtest on panel: one Account, orders only on the day after a decision, metrics from wequant.analytics."""
    strategy = backtest.strategy
    base, last = span(backtest, panel)
    closes = benchmark_closes(backtest, panel, base, last)
    check_cancel(cancel)
    rows = decision_rows(panel.dates, base, last, strategy.period, strategy.every)
    section = select(strategy, panel, rows, cache, on_root=lambda _: check_cancel(cancel))
    account = Account(panel, backtest.cash, panel.dates[base], backtest.execution)
    checkpoint(progress, cancel, Progress(0, last - base, panel.dates[base].item(), account.equity))
    plans, choices = {int(row) + 1: i for i, row in enumerate(rows)}, []
    for t in range(base + 1, last + 1):
        account.begin(t)
        if t in plans:
            choices.append(choose(strategy, section, plans[t], account.shares > 0))
            trade_target(account, targets(section, plans[t], choices[-1]), strategy.band)
        equity = account.end()
        if (t - base) % PROGRESS_DAYS == 0 or t == last:
            checkpoint(progress, cancel, Progress(t - base, last - base, panel.dates[t].item(), equity))
    return assemble(backtest, panel, account.ledger(), section, stack(choices), closes, ENGINE, REBALANCE_VERSION)


def run_form(
    form: Mapping,
    version: int | None = None,
    *,
    store: Store | None = None,
    cache_dir: Path | None = None,
    cache: Cache | None = None,
    progress: Callable[[Progress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Run:
    """The backtest a canonical form describes, on store version version (current if None): from_form, panel_for, run."""
    backtest = Backtest.from_form(form)
    panel = panel_for(backtest, store=store, version=version, cache_dir=cache_dir)
    return run(backtest, panel, cache=cache, progress=progress, cancel=cancel)


def planned_id(backtest: Backtest, panel: Panel) -> str:
    """The id run(backtest, panel) returns, known before it runs."""
    inputs = run_inputs(
        ENGINE,
        backtest.form,
        versions(ENGINE, REBALANCE_VERSION),
        store_version=panel.version,
        data_key=panel.key,
        rules_key=panel.rules_key,
        panel_key=panel_key(panel),
    )
    return run_id(inputs)


def targets(section: Section, i: int, choice: Choice) -> Target:
    """Decision i's choice as a batch target: its weights, 0 for every other held code with a bar, the scores as
    priority, the backups by rank."""
    # a held code without a bar waits for a later decision
    weights = np.where(choice.held & section.traded[i], 0.0, np.nan)
    weights[choice.target] = choice.weight
    backups = np.flatnonzero(choice.backup)
    return Target(weights, section.score[i], backups[np.argsort(section.rank[i, backups])])
