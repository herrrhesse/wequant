"""Account on hand-written markets: the day protocol, D3, T+1, lots, fees, limits, price models and errors."""

import math

import numpy as np
import pytest

from tests.account.fake import FakePanel
from wequant.account import ORDER_REASONS, Account, Execution
from wequant.account import account as account_module

SH, SZ, STAR, BJ = "600000.SH", "000001.SZ", "688001.SH", "830001.BJ"
JAN = ["2024-01-02", "2024-01-03", "2024-01-04"]
INF, NAN = math.inf, math.nan
STATUSES = ("filled", "partial", "rejected")


def one(dates, prices, code=SH, board="main", **grids):
    """A one-code market with open = close = prices unless close is given."""
    close = grids.pop("close", prices)
    return FakePanel(dates, [code], [board], prices, close, **grids)


def li(yuan) -> int:
    return round(yuan * 1000)


def money(fills, k=0) -> tuple:
    """Value, commission, stamp tax, transfer fee and slippage of order k, in 厘."""
    return tuple(
        li(getattr(fills, name)[k]) for name in ("value", "commission", "stamp_tax", "transfer_fee", "slippage")
    )


def yuan(value, commission, stamp, transfer, slippage=0) -> tuple:
    return tuple(li(x) for x in (value, commission, stamp, transfer, slippage))


def outcome(fills, k=0) -> tuple:
    """(status, reason) names of order k."""
    reason = int(fills.reason[k])
    return STATUSES[fills.status[k]], None if reason < 0 else ORDER_REASONS[reason]


def bought(panel, cash, qty=1000, col=0, execution=Execution()) -> Account:
    """An account from the panel's first date that bought qty of col on row 1 and closed the day."""
    account = Account(panel, cash, panel.dates[0], execution)
    account.begin(1)
    account.trade([col], [qty])
    account.end()
    return account


def opened(panel, cash, execution=Execution()) -> Account:
    """An account from the panel's first date with row 1 open."""
    account = Account(panel, cash, panel.dates[0], execution)
    account.begin(1)
    return account


# ---------------------------------------------------------------- U1-U15


def test_d3_keeps_equity_and_values_consistent():
    panel = one(["2023-12-28", "2023-12-29", "2024-01-02"], [20.0, 20.0, 10.0], factor=[1, 1, 2])
    account = opened(panel, 30_000)
    assert money(account.trade([0], [1000])) == yuan(20_000, 6.00, 0, 0.20)
    assert li(account.cash) == li(9_993.80) and li(account.end()) == li(29_993.80)
    account.begin(2)
    assert account.shares[0] == 2000 and account.sellable[0] == 2000 and li(account.equity) == li(29_993.80)
    assert account.values[0] == 20_000 and account.cash + account.values.sum() == pytest.approx(account.equity)
    got = account.trade([0, 0], [500, -300])
    assert money(got, 1) == yuan(3_000, 5.00, 1.50, 0.03) and money(got, 0) == yuan(5_000, 5.00, 0, 0.05)
    assert (account.shares[0], account.sellable[0], li(account.cash)) == (2200, 1700, li(7_982.22))
    assert li(account.equity) == li(29_993.80) and account.values[0] == 22_000
    assert li(account.end()) == li(29_982.22) and li(account.equity) == li(29_982.22)
    swapped = bought(panel, 30_000)
    swapped.begin(2)
    swapped.trade([0, 0], [-300, 500])
    assert (swapped.shares[0], swapped.sellable[0], swapped.cash) == (2200, 1700, account.cash)


def test_t1_binds_across_calls_and_never_within_one():
    panel = one(JAN, [10.0] * 3)
    account = bought(panel, 100_000)
    assert li(account.cash) == li(89_994.90)
    account.begin(2)
    account.trade([0], [500])
    assert li(account.cash) == li(84_989.85) and account.sellable[0] == 1000
    got = account.trade([0], [-INF])
    assert outcome(got) == ("partial", "t1") and got.shares[0] == 1000
    assert (li(account.cash), account.shares[0], account.sellable[0]) == (li(94_979.75), 500, 0)
    assert li(account.end()) == li(99_979.75)
    batch = bought(panel, 100_000)
    batch.begin(2)
    got = batch.trade([0, 0], [500, -INF])
    assert [outcome(got, k) for k in (0, 1)] == [("filled", None)] * 2
    assert (li(batch.cash), batch.shares[0]) == (li(94_979.75), 500)


def test_a_stock_without_a_bar_is_held_at_its_last_close_through_an_ex_date():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    close, open = [5.0, 5.0, NAN, NAN, NAN, 2.6], [5.0, 5.0, NAN, NAN, NAN, 3.0]
    panel = one(dates, open, close=close, factor=[1, 1, 1, 2, 2, 2])
    account = opened(panel, 1_000)
    assert money(account.trade([0], [100])) == yuan(500, 5.00, 0, 0.01)
    assert li(account.cash) == li(494.99) and li(account.end()) == li(994.99)
    for t, shares in zip((2, 3, 4), (100, 200, 200)):
        account.begin(t)
        assert account.shares[0] == shares and account.values[0] == 500
        assert li(account.end()) == li(994.99)
    account.begin(5)
    assert li(account.end()) == li(1_014.99)
    positions = account.ledger().positions
    assert positions["value"].to_list() == [500, 500, 500, 500, 520]
    assert positions["shares"].to_list() == [100, 100, 200, 200, 200]


def test_odd_lots_and_a_fraction_only_exit():
    panel = one(JAN, [10.0, 10.0, 10.01], factor=[1, 1, 1.0625])
    account = bought(panel, 20_000)
    assert li(account.cash) == li(9_994.90)
    account.begin(2)
    assert account.shares[0] == 1062.5
    got = account.trade([0, 0, 0], [-1000, -62, -INF])
    assert money(got, 0) == yuan(10_010, 5.00, 5.01, 0.10)
    assert money(got, 1) == yuan(620.62, 5.00, 0.31, 0.01)
    assert money(got, 2) == yuan(5.01, 0, 0, 0) and got.shares[2] == 0.5
    assert all(outcome(got, k) == ("filled", None) for k in range(3))
    assert li(account.cash) == li(20_615.10) and account.shares[0] == 0
    alone = bought(panel, 20_000)
    alone.begin(2)
    got = alone.trade([0], [-1062])
    assert got.shares[0] == 1062 and money(got) == yuan(10_630.62, 5.00, 5.32, 0.11)
    assert li(alone.cash) == li(20_615.09) and alone.shares[0] == 0.5


def test_a_sale_that_cannot_pay_its_costs_is_rejected():
    panel = one(JAN, [0.05, 0.05, 0.04])
    account = bought(panel, 10.00)
    assert account.cash == 0
    account.begin(2)
    got = account.trade([0], [-INF])
    assert outcome(got) == ("rejected", "cash") and got.price[0] == 0.04
    assert (account.cash, account.shares[0], account.sellable[0]) == (0, 100, 100)


@pytest.mark.parametrize(
    "dates, code, buy, sell, cash",
    [
        (["2014-05-29", "2014-05-30", "2014-06-03"], SH, (5.00, 0, 0.30), (5.00, 10.00, 0.30), (9_994.70, 19_979.40)),
        (["2014-05-29", "2014-05-30", "2014-06-03"], SZ, (5.00, 0, 0.26), (5.00, 10.00, 0.26), (9_994.74, 19_979.48)),
        (["2023-08-23", "2023-08-24", "2023-08-25"], SH, (5.00, 0, 0.10), (5.00, 10.00, 0.10), (9_994.90, 19_979.80)),
        (["2023-08-24", "2023-08-25", "2023-08-28"], SH, (5.00, 0, 0.10), (5.00, 5.00, 0.10), (9_994.90, 19_984.80)),
    ],
)
def test_fees_follow_the_date_and_the_exchange(dates, code, buy, sell, cash):
    account = opened(one(dates, [10.0] * 3, code=code), 20_000)
    assert money(account.trade([0], [1000])) == yuan(10_000, *buy) and li(account.cash) == li(cash[0])
    account.end()
    account.begin(2)
    assert money(account.trade([0], [-INF])) == yuan(10_000, *sell) and li(account.cash) == li(cash[1])


def test_slippage_is_a_cost_and_leaves_the_price():
    account = opened(one(JAN[:2], [10.0] * 2), 20_000, Execution(slippage_bps=10))
    got = account.trade([0], [1000])
    assert money(got) == yuan(10_000, 5.00, 0, 0.10, 10.00) and got.price[0] == 10.0
    assert li(account.cash) == li(9_984.90)


def test_cash_cuts_a_buy_to_the_largest_lot_it_pays_for():
    panel = one(JAN[:2], [10.0] * 2)
    exact = opened(panel, 10_005.10)
    assert outcome(exact.trade([0], [1000])) == ("filled", None) and exact.cash == 0
    short = opened(panel, 10_005.09)
    got = short.trade([0], [1000])
    assert outcome(got) == ("partial", "cash") and got.shares[0] == 900
    assert money(got) == yuan(9_000, 5.00, 0, 0.09) and li(short.cash) == li(1_000)


def test_star_buys_step_by_one_share(monkeypatch):
    panel = one(JAN[:2], [50.0] * 2, code=STAR, board="star")
    calls = []
    real = account_module.trade_fees
    monkeypatch.setattr(account_module, "trade_fees", lambda *args: calls.append(args) or real(*args))
    exact = opened(panel, 12_505.13)
    assert outcome(exact.trade([0], [250])) == ("filled", None) and exact.cash == 0 and len(calls) == 1
    short = opened(panel, 12_505.12)
    got = short.trade([0], [250])
    assert outcome(got) == ("partial", "cash") and got.shares[0] == 249
    assert money(got) == yuan(12_450, 5.00, 0, 0.12) and li(short.cash) == li(50)
    assert len(calls) - 1 <= 1 + 1 + math.ceil(math.log2(51 + 1))  # want, then bisection over 51 lot steps


def test_same_day_sales_pay_for_buys():
    panel = FakePanel(JAN, [SH, SZ], ["main", "main"], [[10.0, 10.0]] * 3, [[10.0, 10.0]] * 3)
    account = bought(panel, 10_100)
    assert li(account.cash) == li(94.90)
    account.begin(2)
    got = account.trade([1, 0], [500, -INF])
    assert money(got, 1) == yuan(10_000, 5.00, 5.00, 0.10)
    assert got.shares[0] == 500 and money(got, 0) == yuan(5_000, 5.00, 0, 0.05)
    assert li(account.cash) == li(5_079.75)


def test_inexact_ratios_are_not_snapped_to_whole_shares():
    panel = one(JAN, [10.0] * 3, factor=[1.1, 1.1, 1.21])
    held = 1000 * (1.21 / 1.1)

    def day2():
        account = bought(panel, 20_000)
        assert li(account.cash) == li(9_994.90)
        account.begin(2)
        assert account.shares[0] == held
        return account

    a = day2()
    assert money(a.trade([0], [-1000])) == yuan(10_000, 5.00, 5.00, 0.10) and li(a.cash) == li(19_984.80)
    assert a.shares[0] == held - 1000
    got = a.trade([0], [-99])
    assert got.shares[0] == 99 and money(got) == yuan(990, 5.00, 0.50, 0.01) and li(a.cash) == li(20_969.29)
    got = a.trade([0], [-INF])
    assert got.shares[0] == held - 1000 - 99 and money(got) == yuan(10.00, 0, 0, 0)
    assert li(a.cash) == li(20_979.29) and a.shares[0] == 0
    for qty in (-INF, -1100):
        b = day2()
        got = b.trade([0], [qty])
        assert got.shares[0] == held and money(got) == yuan(11_000, 5.00, 5.50, 0.11) and li(b.cash) == li(20_984.29)
    c = day2()
    got = c.trade([0], [-1099])
    assert got.shares[0] == 1099 and money(got) == yuan(10_990, 5.00, 5.50, 0.11) and li(c.cash) == li(20_974.29)
    assert c.shares[0] == held - 1099


def test_the_other_side_of_a_limit_passes():
    panel = one(JAN, [10.0] * 3, up=[11.0, 11.0, 10.0], down=[9.0] * 3)
    account = bought(panel, 20_000)
    assert li(account.cash) == li(9_994.90)
    account.begin(2)
    got = account.trade([0], [-INF])
    assert outcome(got) == ("filled", None) and money(got) == yuan(10_000, 5.00, 5.00, 0.10)
    assert li(account.cash) == li(19_984.80)
    floor = opened(one(JAN[:2], [10.0] * 2, up=[11.0] * 2, down=[9.0, 10.0]), 20_000)
    got = floor.trade([0], [1000])
    assert outcome(got) == ("filled", None) and money(got) == yuan(10_000, 5.00, 0, 0.10)
    assert li(floor.cash) == li(9_994.90)


def test_the_close_model_fills_at_the_close():
    panel = one(JAN[:2], [10.0, 11.0], close=[10.0, 10.5], up=[11.0] * 2, down=[9.0] * 2)
    account = opened(panel, 20_000, Execution(price="close"))
    got = account.trade([0], [1000])
    assert money(got) == yuan(10_500, 5.00, 0, 0.11) and got.price[0] == 10.5
    assert li(account.cash) == li(9_494.89) and li(account.end()) == li(19_994.89)
    at_open = opened(panel, 20_000)
    got = at_open.trade([0], [1000])
    assert outcome(got) == ("rejected", "limit_up") and got.price[0] == 11.0 and at_open.cash == 20_000


def test_a_rule_that_does_not_exist_yet_raises_before_anything_fills():
    dates, boards = ["2019-07-18", "2019-07-19"], ["main", "star"]
    panel = FakePanel(dates, [SH, STAR], boards, [[10.0, 30.0]] * 2, [[10.0, 30.0]] * 2)
    account = opened(panel, 100_000)
    with pytest.raises(LookupError):
        account.trade([0, 1], [100, 200])
    assert account.cash == 100_000 and not account.shares.any()
    account.end()
    assert account.ledger().orders.height == 0
    unlisted = FakePanel(dates, [SH, STAR], boards, [[10.0, 30.0], [10.0, NAN]], [[10.0, 30.0], [10.0, NAN]])
    got = opened(unlisted, 100_000).trade([0, 1], [100, 200])
    assert outcome(got, 0) == ("filled", None) and outcome(got, 1) == ("rejected", "no_bar")
    assert np.isnan(got.price[1])


def test_numpy_scalar_cash_and_execution():
    panel = one(JAN[:2], [10.0] * 2)
    for cash in (np.int64(1_000_000), np.float64(1e6)):
        assert Account(panel, cash, JAN[0]).cash == 1_000_000
    scalars = Execution(commission_rate=np.float64(0.0003), min_commission=np.float64(5.0), slippage_bps=np.int64(0))
    runs = [
        opened(panel, np.int64(1_000_000), execution).trade([0, 0], [300, 25_000])
        for execution in (Execution(), scalars)
    ]
    assert all(np.array_equal(a, b) for a, b in zip(*runs))


# ---------------------------------------------------------------- reasons, limits and errors


def test_the_first_matching_check_names_the_reason():
    no_bar = one(JAN[:2], [10.0, NAN], up=[INF, NAN], down=[-INF, NAN])
    got = opened(no_bar, 100_000).trade([0, 0], [-100, 100])
    assert outcome(got, 0) == ("rejected", "no_position") and np.isnan(got.price[0])
    assert outcome(got, 1) == ("rejected", "no_bar")
    capped = one(JAN[:2], [10.0, 11.0], up=[11.0] * 2, down=[9.0] * 2)
    assert outcome(opened(capped, 100).trade([0], [1000])) == ("rejected", "limit_up")
    unknown = one(JAN[:2], [10.0] * 2, up=[NAN] * 2, down=[NAN] * 2)
    assert outcome(opened(unknown, 100_000).trade([0], [100])) == ("rejected", "limit_unknown")
    bj = one(JAN[:2], [10.0, NAN], code=BJ, board="bse")
    assert outcome(opened(bj, 100_000).trade([0], [100])) == ("rejected", "no_bar")


def test_free_limits_never_block():
    panel = one(JAN, [10.0] * 3)
    account = bought(panel, 100_000)
    account.begin(2)
    assert outcome(account.trade([0], [-INF])) == ("filled", None)
    assert outcome(account.trade([0], [100])) == ("filled", None)


def test_protocol_errors():
    panel = one(JAN, [10.0] * 3)
    account = Account(panel, 100_000, JAN[0])
    for call in (lambda: account.trade([0], [100]), account.end, lambda: account.begin(2)):
        with pytest.raises(RuntimeError):
            call()
    account.begin(1)
    for call in (lambda: account.begin(1), lambda: account.begin(2), account.ledger):
        with pytest.raises(RuntimeError):
            call()


@pytest.mark.parametrize(
    "cols, qty, error",
    [
        ([1], [100], IndexError),
        ([-1], [100], IndexError),
        ([0.0], [100], IndexError),
        ([0], [0], ValueError),
        ([0], [NAN], ValueError),
        ([0], [INF], ValueError),
        ([0, 0], [100], ValueError),
    ],
)
def test_bad_orders_raise(cols, qty, error):
    account = opened(one(JAN, [10.0] * 3), 100_000)
    with pytest.raises(error):
        account.trade(cols, qty)


@pytest.mark.parametrize("cash", [0, -1, NAN, INF, 1000.001, True, "1000"])
def test_bad_cash_raises(cash):
    with pytest.raises(ValueError):
        Account(one(JAN, [10.0] * 3), cash, JAN[0])


def test_a_start_off_the_calendar_raises():
    with pytest.raises(KeyError):
        Account(one(JAN, [10.0] * 3), 100_000, "2024-01-01")


def test_a_traded_bj_order_raises_and_changes_nothing():
    panel = FakePanel(JAN[:2], [SH, BJ], ["main", "bse"], [[10.0, 10.0]] * 2, [[10.0, 10.0]] * 2)
    account = opened(panel, 100_000)
    with pytest.raises(LookupError):
        account.trade([0, 1], [100, 100])
    assert account.cash == 100_000 and not account.shares.any()
    account.end()
    assert account.ledger().orders.height == 0


def test_views_are_read_only_and_values_wait_for_a_close():
    panel = one(JAN, [NAN, 10.0, 10.5])
    account = Account(panel, 100_000, JAN[0])
    assert account.equity == 100_000
    account.begin(1)
    account.trade([0], [1000])
    for view in (account.shares, account.sellable, account.values):
        with pytest.raises(ValueError):
            view[0] = 1.0
    assert np.isnan(account.values[0]) and account.equity == 100_000
    account.end()
    assert account.values[0] == 1000 * 10.0
