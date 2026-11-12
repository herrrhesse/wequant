"""Why each order was placed, from the run's own decisions (round 2): each test names its rule."""

import dataclasses

import pytest

from tests.backtest.market import CSI300, ROTATION, WEEK, A, B, build_store
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.expr import REF, Field
from wequant.panel import load_panel
from wequant.runs.causes import with_causes
from wequant.strategy import Factor, Strategy

ADJ = Field("adj_close")
RET1 = Strategy(factors=(Factor("ret1", ADJ / REF(ADJ, 1)),), top_n=1, period="day")  # hold the best one-day return


@pytest.fixture(scope="module")
def rotated(tmp_path_factory):
    """A and B take turns at the best one-day return: buy A, swap to B, swap back to A."""
    home = tmp_path_factory.mktemp("rotation")
    store = build_store(home / "home", WEEK, ROTATION, index=CSI300)
    panel = load_panel(store=store, cache_dir=home / "cache")
    return run(Backtest(RET1, cash=100_000, execution=Execution(slippage_bps=0.0)), panel)


def test_each_order_takes_its_cause_from_the_decision_the_day_before(rotated):
    """Ranks by hand: Jan 3 A 1.05 > B 0.975; Jan 4 B 1.077 > A 0.971; Jan 5 A 1.059 > B 1.024 (top 1 held)."""
    found = with_causes(rotated).select("code", "side", "cause", "rank", "limit").rows()
    assert found == [
        (A, "buy", "enter", 1, None),
        (A, "sell", "exit_rank", 2, 1),
        (B, "buy", "enter", 1, None),
        (B, "sell", "exit_rank", 2, 1),
        (A, "buy", "enter", 1, None),
    ]


def test_an_order_no_decision_explains_is_unrecorded(rotated):
    bare = dataclasses.replace(rotated, detail=rotated.detail.clear(), picks=rotated.picks.clear())
    assert set(with_causes(bare)["cause"]) == {"unrecorded"}
