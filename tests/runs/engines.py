"""Small engines for the archive tests: hold one stock on a three-day fake panel or on a store's panel."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from tests.account.fake import FakePanel
from wequant.account import Account, Execution, Ledger
from wequant.analytics import MetricSpec, compute_metrics
from wequant.backtest.anomalies import ANOMALY_USES
from wequant.data.store import Store
from wequant.expr import panel_key
from wequant.panel import load_panel
from wequant.runs import Engine, Run

DESC = {"code": "600000.SH", "shares": 1000, "cash": 100000.0}
NO_SLIPPAGE = Execution(slippage_bps=0.0)
PICKS = {"date": pl.Date, "code": pl.String, "rank": pl.Int64}
FUNNEL = {"date": pl.Date, "step": pl.String}
DETAIL = {"date": pl.Date, "code": pl.String}


def hold(description: dict, store_version: int, sell: float = 0) -> Run:
    """Buy description["shares"] of 600000.SH on 2024-01-03, sell `sell` of them on 2024-01-04."""
    panel = FakePanel(
        ["2024-01-02", "2024-01-03", "2024-01-04"], ["600000.SH"], ["main"], [10.0, 10.0, 10.4], [10.0, 10.5, 10.2]
    )
    panel.version = store_version
    account = Account(panel, description["cash"], "2024-01-02", NO_SLIPPAGE)
    account.begin(1)
    account.trade([0], [description["shares"]])
    account.end()
    account.begin(2)
    if sell:
        account.trade([0], [-sell])
    account.end()
    return held_run(description, panel, account.ledger())


def buggy(description: dict, store_version: int) -> Run:
    """hold with a bug that changes its results: it sells 500 shares on the last day."""
    return hold(description, store_version, sell=500)


def store_engine(store: Store, cache_dir: Path) -> Engine:
    """An engine that buys description["qty"] of description["code"] on row 1 and holds to the panel's last day."""

    def engine(description: dict, version: int) -> Run:
        panel = load_panel(None, description["end"], version=version, store=store, cache_dir=cache_dir)
        account = Account(panel, description["cash"], panel.dates[0], NO_SLIPPAGE)
        column = panel.codes.tolist().index(description["code"])
        for t in range(1, len(panel.dates)):
            account.begin(t)
            if t == 1:
                account.trade([column], [description["qty"]])
            account.end()
        return held_run(description, panel, account.ledger())

    return engine


def held_run(description: dict, panel, ledger: Ledger) -> Run:
    """The "hold" Run of a closed account: one pick on the first day, an empty funnel and detail, no warnings."""
    calendar = pl.Series("date", panel.dates)
    metrics = compute_metrics(ledger.daily, calendar, positions=ledger.positions, orders=ledger.orders)
    anchor = panel.dates[0].item()
    picks = pl.DataFrame({"date": [anchor], "code": [description["code"]], "rank": [1]}, schema=PICKS)
    frames = (picks, pl.DataFrame(schema=FUNNEL), pl.DataFrame(schema=DETAIL), pl.DataFrame(schema=ANOMALY_USES))
    return Run("hold", description, {"hold": 1}, panel_key(panel), anchor, ledger, metrics, MetricSpec(), *frames, {})
