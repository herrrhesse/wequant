"""The batch protocol: hand-computed goldens on small fake markets, plus its ordering rules as properties."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.account.fake import FakePanel
from wequant.account import FILLED, PARTIAL, REJECTED, Account, Execution, Target, trade_target

NAN, INF = np.nan, np.inf
EX0 = Execution(slippage_bps=0.0)
NONE = np.array([], dtype=np.int64)
DAYS2, DAYS3 = ["2024-01-02", "2024-01-03"], ["2024-01-02", "2024-01-03", "2024-01-04"]


def opened(panel, cash=100_000, execution=EX0, day1=None):
    """An account on panel, day 1 open, after an optional first-day batch (cols, qty) closed on day 1 and day 2 open."""
    account = Account(panel, cash, panel.dates[0], execution)
    account.begin(1)
    if day1 is not None:
        account.trade(*day1)
        account.end()
        account.begin(2)
    return account


def rows(batches):
    """Each order of each call as (col, qty, shares, price, value, commission, stamp, transfer, status, reason)."""
    return [
        [
            (
                int(c),
                float(q),
                *(float(getattr(b.fills, f)[k]) for f in FIELDS),
                int(b.fills.status[k]),
                int(b.fills.reason[k]),
            )
            for k, (c, q) in enumerate(zip(b.cols, b.qty))
        ]
        for b in batches
    ]


FIELDS = ("shares", "price", "value", "commission", "stamp_tax", "transfer_fee")


def test_buys_share_the_cash_and_a_column_without_a_fill_price_is_refused_no_bar():
    panel = FakePanel(
        DAYS2,
        ["600000.SH", "000001.SZ", "600004.SH"],
        ["main"] * 3,
        [[10, 20, 5], [10, 20, NAN]],
        [[10, 20, 5], [10, 20, NAN]],
    )
    account = opened(panel)
    got = trade_target(account, Target(np.array([0.6, 0.6, 0.2]), np.array([3.0, 2.0, 1.0]), NONE))
    (buys,) = rows(got)
    assert buys[0] == (0, 4285.0, 4200.0, 10.0, 42000.0, 12.6, 0.0, 0.42, FILLED, -1)
    assert buys[1] == (1, 2142.0, 2100.0, 20.0, 42000.0, 12.6, 0.0, 0.42, FILLED, -1)
    assert buys[2][:3] == (2, 2857.0, 0.0) and np.isnan(buys[2][3]) and buys[2][8:] == (REJECTED, 1)
    assert account.cash == pytest.approx(15973.96, abs=1e-9)


@pytest.mark.parametrize(("band", "traded"), [(0.10, [0, 1]), (0.0, [0, 1]), (0.13, [1]), (0.15, []), (None, [])])
def test_band_trades_held_targets_outside_it(band, traded):
    panel = FakePanel(
        DAYS3,
        ["600000.SH", "000001.SZ"],
        ["main"] * 2,
        [[10, 10], [10, 10], [10.5, 9.5]],
        [[10, 10], [10, 10], [10.5, 9.5]],
    )
    account = opened(panel, day1=([0, 1], [5600, 4300]))
    assert account.cash == pytest.approx(969.31, abs=1e-9) and account.equity == pytest.approx(99969.31, abs=1e-9)
    got = rows(trade_target(account, Target(np.array([0.5, 0.5]), np.array([2.0, 1.0]), NONE), band))
    assert [order[0] for call in got for order in call] == traded
    if band in (0.10, 0.0):
        assert got[0] == [(0, -572.0, 500.0, 10.5, 5250.0, 5.0, 2.63, 0.05, FILLED, -1)]
        assert got[1] == [(1, 653.0, 600.0, 9.5, 5700.0, 5.0, 0.0, 0.06, FILLED, -1)]
        assert account.cash == pytest.approx(506.57, abs=1e-9)
    if band == 0.13:
        assert got == [[(1, 102.0, 100.0, 9.5, 950.0, 5.0, 0.0, 0.01, FILLED, -1)]]
        assert account.cash == pytest.approx(14.30, abs=1e-9)


def test_exit_top_up_priority_and_a_stand_in_for_a_limit_up_refusal():
    codes = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"]
    opens = [[10.0, 20.0, 5.0, 8.0, 4.0], [10.0, 20.0, 5.0, 8.0, 4.0], [10.2, 21.8, 5.1, 8.8, 4.0]]
    closes = [[10.0, 20.0, 5.0, 8.0, 4.0], [10.0, 22.0, 5.0, 8.0, 4.0], [10.3, 22.0, 5.2, 8.8, 4.1]]
    up = [[INF] * 5, [INF] * 5, [11.22, 24.2, 5.5, 8.8, 4.4]]
    down = [[-INF] * 5, [-INF] * 5, [9.18, 19.8, 4.5, 7.2, 3.6]]
    panel = FakePanel(["2024-03-01", "2024-03-04", "2024-03-05"], codes, ["main"] * 5, opens, closes, up=up, down=down)
    account = opened(panel, day1=([0, 1], [1000, 1000]))
    assert account.equity == pytest.approx(101988.70, abs=1e-9)
    weights, priority = np.array([0.0, 0.25, 0.25, 0.25, NAN]), np.array([0.0, 0.8, 0.9, 0.7, 0.0])
    got = rows(trade_target(account, Target(weights, priority, np.array([4])), 0.1))
    assert got[0] == [(0, -INF, 1000.0, 10.2, 10200.0, 5.0, 5.1, 0.1, FILLED, -1)]
    assert got[1] == [
        (2, 4999.0, 4900.0, 5.1, 24990.0, 7.5, 0.0, 0.25, FILLED, -1),
        (1, 160.0, 100.0, 21.8, 2180.0, 5.0, 0.0, 0.02, FILLED, -1),
        (3, 2897.0, 0.0, 8.8, 0.0, 0.0, 0.0, 0.0, REJECTED, 3),
    ]
    assert got[2] == [(4, 6374.0, 6300.0, 4.0, 25200.0, 7.56, 0.0, 0.25, FILLED, -1)]
    assert account.cash == pytest.approx(27787.92, abs=1e-9)
    assert account.end() == pytest.approx(103297.92, abs=1e-9)


def test_a_held_target_is_left_alone_without_a_band_and_new_buys_scale_to_the_cash():
    panel = FakePanel(DAYS3, ["600010.SH", "600011.SH"], ["main"] * 2, [[10, 10]] * 3, [[10, 10]] * 3)
    account = opened(panel, cash=10_000, day1=([0], [800]))
    assert account.cash == pytest.approx(1994.92, abs=1e-9)
    got = rows(trade_target(account, Target(np.array([0.5, 0.5]), np.array([1.0, 1.0]), NONE)))
    assert got == [[(1, 199.0, 100.0, 10.0, 1000.0, 5.0, 0.0, 0.01, FILLED, -1)]]
    assert account.cash == pytest.approx(989.91, abs=1e-9)


def test_slippage_is_a_cost_so_a_whole_equity_buy_is_cut_by_cash():
    panel = FakePanel(DAYS2, ["600000.SH"], ["main"], [[10.0], [10.0]], [[10.0], [10.0]])
    account = opened(panel, execution=Execution(slippage_bps=10.0))
    (call,) = trade_target(account, Target(np.array([1.0]), np.array([1.0]), NONE))
    assert call.qty.tolist() == [10000.0] and call.fills.shares.tolist() == [9900.0]
    assert call.fills.slippage.tolist() == [99.0] and call.fills.status.tolist() == [PARTIAL]
    assert account.cash == pytest.approx(870.31, abs=1e-9)


def test_nothing_to_do_makes_no_call_and_no_ledger_row():
    panel = FakePanel(DAYS2, ["600000.SH"], ["main"], [[10.0], [10.0]], [[10.0], [10.0]])
    account = opened(panel)
    assert trade_target(account, Target(np.array([NAN]), np.array([0.0]), NONE)) == []
    account.end()
    assert account.ledger().orders.height == 0


def test_a_backup_sold_at_the_open_stands_in_again_for_a_target_refused_again():
    up, down = [[INF, INF], [22.0, 11.0], [23.65, 12.1]], [[-INF, -INF], [18.0, 9.0], [19.35, 9.9]]
    opens, closes = [[20.0, 10.0], [21.0, 11.0], [21.5, 12.1]], [[20.0, 10.0], [21.5, 11.0], [21.6, 12.1]]
    panel = FakePanel(DAYS3, ["000001.SZ", "600000.SH"], ["main"] * 2, opens, closes, up=up, down=down)
    account = opened(panel, day1=([0], [4700]))
    assert account.equity == pytest.approx(102319.40, abs=1e-9)
    got = rows(trade_target(account, Target(np.array([0.0, 1.0]), np.array([0.5, 1.0]), np.array([0]))))
    assert got == [
        [(0, -INF, 4700.0, 21.5, 101050.0, 30.32, 50.53, 1.01, FILLED, -1)],
        [(1, 8449.0, 0.0, 12.1, 0.0, 0.0, 0.0, 0.0, REJECTED, 3)],
        [(0, 4755.0, 4700.0, 21.5, 101050.0, 30.32, 0.0, 1.01, FILLED, -1)],
    ]
    assert account.cash == pytest.approx(1156.21, abs=1e-9)
    assert account.end() == pytest.approx(102676.21, abs=1e-9)


def test_a_held_target_without_any_price_needs_one_only_to_be_resized():
    grid = [[10.0, 10.0], [10.0, 10.0], [NAN, 10.0], [NAN, 10.0]]
    panel = FakePanel([*DAYS3, "2024-01-05"], ["600000.SH", "600004.SH"], ["main"] * 2, grid, grid)
    target = Target(np.array([0.3, 0.5]), np.array([1.0, 0.5]), NONE)
    accounts = []
    for _ in range(2):
        account = opened(panel, day1=([0], [1000]))
        account.end()
        account.begin(3)
        accounts.append(account)
    got = rows(trade_target(accounts[0], target))
    assert got == [[(1, 4999.0, 4900.0, 10.0, 49000.0, 14.7, 0.0, 0.49, FILLED, -1)]]
    assert accounts[0].cash == pytest.approx(40979.71, abs=1e-9)
    with pytest.raises(ValueError, match="neither a fill price"):
        trade_target(accounts[1], target, 0.0)


def test_refusals():
    panel = FakePanel(DAYS2, ["600000.SH", "600004.SH"], ["main"] * 2, [[10.0, NAN], [10.0, NAN]], [[10.0, NAN]] * 2)
    account = Account(panel, 100_000, panel.dates[0], EX0)
    with pytest.raises(RuntimeError, match="first trade"):
        trade_target(account, Target(np.array([0.5, NAN]), np.zeros(2), NONE))
    account.begin(1)
    with pytest.raises(ValueError, match="neither a fill price"):
        trade_target(account, Target(np.array([0.5, 0.5]), np.array([1.0, 1.0]), NONE))
    with pytest.raises(ValueError, match="negative"):
        trade_target(account, Target(np.array([-0.1, NAN]), np.zeros(2), NONE))
    with pytest.raises(ValueError, match="backups"):
        trade_target(account, Target(np.array([NAN, NAN]), np.zeros(2), np.array([2])))
    account.trade([0], [100])
    with pytest.raises(RuntimeError, match="first trade"):
        trade_target(account, Target(np.array([0.5, NAN]), np.zeros(2), NONE))


def test_a_sell_earlier_the_same_day_counts_as_a_trade():
    panel = FakePanel(DAYS3, ["600000.SH", "600004.SH"], ["main"] * 2, [[10.0, 10.0]] * 3, [[10.0, 10.0]] * 3)
    account = opened(panel, day1=([0], [1000]))
    assert account.fresh
    account.trade([0], [-500])
    assert not account.fresh
    with pytest.raises(RuntimeError, match="first trade"):
        trade_target(account, Target(np.array([NAN, 0.5]), np.zeros(2), NONE))


@given(
    held=st.lists(st.integers(0, 3), min_size=4, max_size=4),
    weights=st.lists(st.one_of(st.just(NAN), st.just(0.0), st.floats(0.01, 0.6)), min_size=4, max_size=4),
    priority=st.lists(st.sampled_from([0.1, 0.5, 0.9]), min_size=4, max_size=4),
    opens=st.lists(st.sampled_from([5.0, 9.5, 10.0, 21.0]), min_size=4, max_size=4),
)
def test_sells_come_first_by_column_then_buys_by_priority(held, weights, priority, opens):
    codes = ["600001.SH", "600002.SH", "600003.SH", "600004.SH"]
    panel = FakePanel(DAYS3, codes, ["main"] * 4, [[10.0] * 4, [10.0] * 4, opens], [[10.0] * 4, [10.0] * 4, opens])
    lots = [(j, 100.0 * h) for j, h in enumerate(held) if h]
    account = opened(panel, cash=1_000_000, day1=([j for j, _ in lots], [q for _, q in lots]) if lots else ([], []))
    weights, priority = np.array(weights), np.array(priority)
    got = trade_target(account, Target(weights, priority, np.array([], dtype=np.int64)), 0.0)
    sells = [batch for batch in got if (batch.qty < 0).any()]
    assert all(batch is got[0] and (batch.qty < 0).all() and np.all(np.diff(batch.cols) > 0) for batch in sells)
    for batch in got[len(sells) :]:
        keys = list(zip(-priority[batch.cols], batch.cols))
        assert (batch.qty > 0).all() and keys == sorted(keys)
    assert not {int(c) for batch in got for c in batch.cols} & set(np.flatnonzero(np.isnan(weights)).tolist())
