"""Weekly and monthly rebalance strategies trade exactly as before round 4: their run ids, fills and equity are pinned
from the code before S4.8 (main 4a12f03), on a synthetic market."""

import hashlib
import json

import pytest

from tests.backtest.market import build_store, mixed_market
from wequant.backtest import Backtest
from wequant.backtest.backtest import panel_for
from wequant.backtest.engine import run
from wequant.strategy import Strategy

STRATEGY = {
    "filters": [{"name": "cheap", "expr": ["LT", "close", 18.0]}],
    "factors": [{"name": "mom", "expr": ["DIV", "adj_close", ["REF", "adj_close", 3]]}],
    "top_n": 2,
}
PINNED = {  # period -> (run id, sha256 of the orders and the daily equity), from main 4a12f03
    "week": ("fb45d021c1778809", "08c75cf74ca54bdb9a485cfd457cef7f4237d94fe032f5603ce9b3c95a0e5dbf"),
    "month": ("21c7b51ea02178f9", "ece0b0854a80c6fad052eaa24e97d8a608cdccd75297585ca065c72c5591a89a"),
}


def outcome(home, cache, period: str) -> tuple[str, str]:
    """The run id and a digest of the fills and equity of STRATEGY rebalanced every period on mixed_market(40)."""
    days, bars = mixed_market(40)
    store = build_store(home, days, bars)
    backtest = Backtest(Strategy.from_form({**STRATEGY, "period": period}), benchmark=None)
    found = run(backtest, panel_for(backtest, store=store, cache_dir=cache))
    rows = {"orders": found.ledger.orders.to_dicts(), "equity": found.ledger.daily["equity"].to_list()}
    return found.id, hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


@pytest.mark.parametrize("period", PINNED)
def test_a_rebalance_strategy_trades_as_before_weekly_bars_existed(tmp_path, period):
    assert outcome(tmp_path / "home", tmp_path / "cache", period) == PINNED[period]
