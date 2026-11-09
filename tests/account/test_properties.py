"""Random markets and order streams: the account's invariants and an order-by-order oracle hold on every draw."""

import datetime as dt
import math

import numpy as np
import polars as pl
from hypothesis import given
from hypothesis import strategies as st

from tests.account.fake import FakePanel
from wequant.account import ORDER_REASONS, PRICES, Account, Execution
from wequant.analytics import compute_metrics
from wequant.analytics.inputs import ORDER_STATUSES
from wequant.rules import default_book, max_buy, max_sell, trade_fees

STARTS = {dt.date(2014, 6, 2): False, dt.date(2023, 8, 21): True, dt.date(2024, 1, 2): True}  # STAR has a lot rule
KINDS = (("600{:03d}.SH", "main"), ("000{:03d}.SZ", "main"), ("300{:03d}.SZ", "chinext"), ("688{:03d}.SH", "star"))
STEPS = (1, 1, 1, 1.25, 2, 0.5, 1.0625, 1.1, 1.0329)  # the last two are not exact in binary
NO_COSTS = (0, 0, 0, 0)
MONEY = ("value", "commission", "stamp_tax", "transfer_fee", "slippage")


# ---------------------------------------------------------------- random runs


@st.composite
def scenarios(draw):
    """A random market, initial cash, execution and the calls of each day; one seeded generator fills in cells and orders."""
    rows, n = draw(st.integers(3, 12)), draw(st.integers(1, 4))
    start = draw(st.sampled_from(sorted(STARTS)))
    kinds = draw(st.lists(st.sampled_from(KINDS if STARTS[start] else KINDS[:3]), min_size=n, max_size=n))
    execution = Execution(draw(st.sampled_from(PRICES)), slippage_bps=draw(st.integers(0, 50)))
    rng = np.random.default_rng(draw(st.integers(0, 2**32 - 1)))
    cash = int(10_000 * 100 ** rng.random())  # log-uniform: some runs run out of cash, some never do
    return market(rng, weekdays(start, rows), kinds), cash, execution, calls(rng, rows, n)


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    return [day for day in (start + dt.timedelta(i) for i in range(2 * n + 2)) if day.weekday() < 5][:n]


def market(rng, dates, kinds) -> FakePanel:
    """Whole-fen prices traded with probability 0.8, random limits and factors stepping by STEPS."""
    shape = (len(dates), len(kinds))
    traded = rng.random(shape) < 0.8
    open, close = (np.where(traded, rng.integers(100, 5001, shape) / 100, np.nan) for _ in range(2))
    steps = rng.choice(STEPS, shape)
    steps[0] = 1.0
    codes, boards = [code.format(j) for j, (code, _) in enumerate(kinds)], [board for _, board in kinds]
    return FakePanel(dates, codes, boards, open, close, np.cumprod(steps, axis=0), *limits(rng, open, close))


def limits(rng, open, close) -> tuple[np.ndarray, np.ndarray]:
    """Per traded cell: finite bounds at the open, the close or beyond both; FREE; or unknown."""
    state = rng.choice(3, open.shape, p=[0.5, 0.4, 0.1])  # finite, FREE, unknown
    gap = rng.integers(1, 200, open.shape) / 100
    at = [0.2, 0.2, 0.6]
    up = np.choose(rng.choice(3, open.shape, p=at), [open, close, np.fmax(open, close) + gap])
    down = np.choose(rng.choice(3, open.shape, p=at), [open, close, np.fmin(open, close) - gap])
    bound = [np.select([state == 0, state == 1], [b, free], np.nan) for b, free in ((up, np.inf), (down, -np.inf))]
    for b in bound:
        b[np.isnan(open)] = np.nan
    return bound[0], bound[1]


def calls(rng, rows: int, n: int) -> dict[int, list[list[tuple]]]:
    """0..5 random orders a day from row 1, sent in one call or split in two."""
    days = {}
    for t in range(1, rows):
        orders = [(int(rng.integers(0, n)), quantity(rng)) for _ in range(rng.integers(0, 6))]
        if rng.random() < 0.5:  # buys first, so that a second call meets shares bought today
            orders.sort(key=lambda order: order[1] < 0)
        cut = int(rng.integers(1, len(orders))) if len(orders) > 1 and rng.random() < 0.5 else len(orders)
        days[t] = [list(zip(*part)) for part in (orders[:cut], orders[cut:]) if part]
    return days


def quantity(rng) -> float:
    if rng.random() < 0.5:
        return float(rng.integers(1, 3001))
    return -math.inf if rng.random() < 0.5 else -float(rng.integers(1, 3001))


# ---------------------------------------------------------------- the oracle


class Mirror:
    """The cash and shares the test expects the account to hold, moved fill by fill."""

    def __init__(self, account: Account) -> None:
        self.cash = round(account.cash * 1000)
        self.sellable = account.sellable.copy()
        self.bought, self.sold = np.zeros_like(self.sellable), np.zeros_like(self.sellable)

    @property
    def shares(self) -> np.ndarray:
        return self.sellable + self.bought

    def apply(self, j: int, sell: bool, got: float, money: tuple) -> None:
        value, costs = money[0], sum(money[1:])
        if sell:
            self.cash += value - costs
            self.sellable[j] = 0.0 if got == self.sellable[j] else self.sellable[j] - got
            self.sold[j] += got
        else:
            self.cash -= value + costs
            self.bought[j] += got


class Day:
    """Row t of a panel as the order path reads it."""

    def __init__(self, panel: FakePanel, execution: Execution, t: int) -> None:
        self.panel, self.execution, self.t = panel, execution, t
        self.date = panel.dates[t].item()
        self.price = panel.raw(execution.price)[t]

    def lot(self, j: int):
        return default_book().in_force("lot", self.panel.boards[j], self.date)

    def money(self, j: int, side: str, got: float) -> tuple:
        """Value and the four costs in 厘 of got shares at the day's price, recomputed from the rules."""
        if got == 0:
            return (0, *NO_COSTS)
        li, whole = round(self.price[j] * 1000), math.floor(got)
        value = whole * li + math.floor((got - whole) * li / 10 + 0.5) * 10
        if whole == 0:
            return (value, *NO_COSTS)
        fees = trade_fees(default_book(), self.panel.exchanges[j], self.date, side, whole, li)
        e = self.execution
        return value, e.commission(whole * li), fees.stamp_tax, fees.transfer_fee, e.slippage(whole * li)


def expected(day: Day, mirror: Mirror, j: int, q: float) -> tuple:
    """(status, reason, shares) of order (j, q) by the order path, the first matching check deciding."""
    p, panel, t, sell = day.price[j], day.panel, day.t, q < 0
    if sell and mirror.shares[j] <= 0:
        return "rejected", "no_position", 0.0
    if not (panel.traded[t, j] and p > 0):
        return "rejected", "no_bar", 0.0
    if np.isnan(panel.limit_up_price[t, j]):
        return "rejected", "limit_unknown", 0.0
    if not sell and p >= panel.limit_up_price[t, j]:
        return "rejected", "limit_up", 0.0
    if sell and p <= panel.limit_down_price[t, j]:
        return "rejected", "limit_down", 0.0
    return expected_sell(day, mirror, j, -q) if sell else expected_buy(day, mirror, j, q)


def expected_sell(day: Day, mirror: Mirror, j: int, q: float) -> tuple:
    sellable = mirror.sellable[j]
    if sellable <= 0:
        return "rejected", "t1", 0.0
    got = max_sell(day.lot(j), min(q, sellable), sellable)
    if got == 0:
        return "rejected", "lot", 0.0
    value, *costs = day.money(j, "sell", got)
    if mirror.cash + value - sum(costs) < 0:
        return "rejected", "cash", 0.0
    return ("partial", "t1", got) if q > sellable and mirror.bought[j] > 0 else ("filled", None, got)


def expected_buy(day: Day, mirror: Mirror, j: int, q: float) -> tuple:
    """Trims one lot step at a time from the largest size cash covers before costs."""
    lot, li = day.lot(j), round(day.price[j] * 1000)
    want = max_buy(lot, math.floor(q))
    if want == 0:
        return "rejected", "lot", 0.0
    size = max_buy(lot, min(want, mirror.cash // li))
    while size and sum(day.money(j, "buy", size)) > mirror.cash:
        size = size - lot.step if size - lot.step >= lot.min_qty else 0
    if size == 0:
        return "rejected", "cash", 0.0
    return ("filled", None, size) if size == want else ("partial", "cash", size)


def check_call(day: Day, mirror: Mirror, cols: list[int], qty: list[float], fills) -> None:
    """Replays a call in execution order, every sell then every buy, checking each order against the state before it."""
    assert len(fills.shares) == len(cols)
    for k in [k for k, q in enumerate(qty) if q < 0] + [k for k, q in enumerate(qty) if q > 0]:
        j, q, got = cols[k], qty[k], fills.shares[k]
        reason = None if fills.reason[k] < 0 else ORDER_REASONS[fills.reason[k]]
        assert (ORDER_STATUSES[fills.status[k]], reason, got) == expected(day, mirror, j, q)
        priced = day.panel.traded[day.t, j] and day.price[j] > 0
        assert (fills.price[k] == day.price[j]) if priced else np.isnan(fills.price[k])
        if got:
            check_fill(day, mirror, j, q, got)
        side = "sell" if q < 0 else "buy"
        money = day.money(j, side, got)
        assert tuple(round(getattr(fills, name)[k] * 1000) for name in MONEY) == money
        mirror.apply(j, q < 0, got, money)


def check_fill(day: Day, mirror: Mirror, j: int, q: float, got: float) -> None:
    """A fill has a bar, a known limit and the side of it that trades, and a size the lot rule allows."""
    t, panel, p = day.t, day.panel, day.price[j]
    assert panel.traded[t, j] and panel.limit_known[t, j]
    if q > 0:
        assert p < panel.limit_up_price[t, j] and max_buy(day.lot(j), int(got)) == got
    else:
        assert p > panel.limit_down_price[t, j] and got <= mirror.sellable[j]
        assert got == mirror.sellable[j] or max_sell(day.lot(j), got, mirror.sellable[j]) == got


# ---------------------------------------------------------------- the run


def run(panel: FakePanel, cash: int, execution: Execution, days: dict):
    """The ledger of a run, with every invariant and oracle check applied along the way."""
    account = Account(panel, cash, panel.dates[0], execution)
    for t in range(1, len(panel.dates)):
        closed = account.shares
        account.begin(t)
        ratio = panel.factor[t] / panel.factor[t - 1]
        assert np.array_equal(account.shares, closed * ratio) and np.array_equal(account.sellable, account.shares)
        assert abs(account.cash + account.values.sum() - account.equity) <= 1e-9 * account.equity
        mirror, day = Mirror(account), Day(panel, execution, t)
        opened = account.sellable
        for cols, qty in days[t]:
            check_call(day, mirror, cols, qty, account.trade(cols, qty))
            assert round(account.cash * 1000) == mirror.cash and mirror.cash >= 0 and mirror.cash % 10 == 0
            assert np.allclose(account.shares, mirror.shares, rtol=0, atol=1e-9) and (account.shares >= 0).all()
            assert np.allclose(account.sellable, mirror.sellable, rtol=0, atol=1e-9)
        assert (mirror.sold <= opened + 1e-9).all()
        moved = closed * ratio + mirror.bought - mirror.sold
        assert (np.abs(account.shares - moved) <= 1e-9 + 1e-12 * account.shares).all()
        account.end()
    return account.ledger()


def check_ledger(ledger, panel: FakePanel, cash: int, days: dict) -> None:
    orders, daily = ledger.orders, ledger.daily
    assert orders.height == sum(len(cols) for calls in days.values() for cols, _ in calls)
    assert ((orders["status"] == "rejected") == (orders["shares"] == 0)).all()
    assert orders.filter(pl.col("status") == "partial")["reason"].is_in(["t1", "cash"]).all()
    li = {name: (orders[name] * 1000).round().cast(pl.Int64) for name in MONEY}
    costs = li["commission"] + li["stamp_tax"] + li["transfer_fee"] + li["slippage"]
    sign = pl.Series(np.where(orders["side"] == "sell", 1, -1))
    flows = (sign * li["value"] - costs).sum()
    assert cash * 1000 + flows == round(daily["cash"][-1] * 1000)
    held = ledger.positions.group_by("date").agg(pl.col("value").sum())
    marked = daily.join(held, on="date", how="left").with_columns(pl.col("value").fill_null(0.0))
    assert ((marked["equity"] - marked["cash"] - marked["value"]).abs() <= 1e-9 * marked["equity"]).all()
    assert (ledger.positions["shares"] > 0).all()
    compute_metrics(daily, pl.Series("date", panel.dates), positions=ledger.positions, orders=orders)


@given(scenarios())
def test_invariants_and_the_oracle_hold(scenario):
    panel, cash, execution, days = scenario
    check_ledger(run(panel, cash, execution, days), panel, cash, days)


@given(scenarios())
def test_runs_are_deterministic(scenario):
    first, second = run(*scenario), run(*scenario)
    assert all(getattr(first, name).equals(getattr(second, name)) for name in ("daily", "positions", "orders"))
    assert first.meta == second.meta
