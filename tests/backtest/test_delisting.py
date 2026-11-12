"""G-D17, hand-computed runs of delisted holdings through the engine (each test names the rule it guards); the
treatments themselves are tested on the account."""

import datetime as dt

import polars as pl
import pytest

from tests.backtest.market import build_store, business_days
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.data.delistings import DELIST_DATE, LIST_DATE
from wequant.expr import Field
from wequant.panel import load_panel
from wequant.strategy import Condition, Factor, Strategy

DAYS = business_days(30)
D = [dt.date.fromisoformat(day) for day in DAYS]
F, M, S, B = "600001.SH", "600002.SH", "600003.SH", "000001.SZ"  # forced, merger, long suspension, never bought
NAMES = {F: "退市甲", M: "乙股份", S: "丙股份", B: "丁股份"}
LAST = {F: 10.0, M: 8.0, S: 9.0}  # every bar of a code opens and closes at its price
EX0 = Execution(slippage_bps=0.0)
CHEAP = Strategy(  # day 0 only: the three cheap codes, a quarter each
    filters=(Condition("cheap", Field("close") < 15.0),),
    factors=(Factor("price", Field("close"), prefer="low"),),
    top_n=4,
    period="day",
    every=1000,
)
SHARES = {F: 25_000.0, M: 31_200.0, S: 27_700.0}  # a quarter of 1,000,000 at each price, in lots of 100
COSTS = 77.50 + 77.38 + 77.28  # commission 0.03% and transfer fee 0.001% of 250,000, 249,600 and 249,300
HELD = 1_000_000 - COSTS  # equity while all three are held: 999,767.84
CASH = HELD - 250_000 - 249_600 - 249_300  # 250,867.84


def securities(store, delist_dates: dict | None) -> pl.DataFrame:
    """The store's securities with names, the three cheap codes' data ending on day 2, and official dates if given:
    every code listed on day 0, the given ones delisted on their dates."""
    ended = pl.when(pl.col("code").is_in([F, M, S])).then(pl.lit(D[2])).otherwise(pl.col("last_date"))
    frame = store.read_table("securities").with_columns(last_date=ended, name=pl.col("code").replace_strict(NAMES))
    if delist_dates is None:
        return frame
    dates = pl.col("code").replace_strict(delist_dates, default=None, return_dtype=pl.Date)
    return frame.with_columns(dates.alias(DELIST_DATE), pl.lit(D[0]).alias(LIST_DATE))


def market(home, delist_dates: dict | None = None):
    """The 30-day market: F, M and S trade days 0 to 2, B every day; with delist_dates the store has official dates."""
    bars = {code: [(price, price)] * 3 + [None] * 27 for code, price in LAST.items()} | {B: [(20.0, 20.0)] * 30}
    store = build_store(home / "home", DAYS, bars)
    store.commit({}, replace={"securities": securities(store, delist_dates)})
    return load_panel(store=store, cache_dir=home / "cache")


@pytest.fixture(scope="module")
def official(tmp_path_factory):
    return market(tmp_path_factory.mktemp("official"), {F: D[5], M: D[5]})


@pytest.fixture(scope="module")
def approximate(tmp_path_factory):
    return market(tmp_path_factory.mktemp("approximate"))


def run_on(panel, execution=EX0):
    return run(Backtest(CHEAP, cash=1_000_000, execution=execution, benchmark=None), panel)


def equity(result, day):
    return result.ledger.daily.filter(pl.col("date") == day)["equity"].item()


def test_auto_writes_down_the_forced_delisting_cashes_the_merger_and_holds_the_suspension(official):
    """v4 D17 auto, at the official day's open, before the day's rebalance rows; forced rows are no rebalance orders."""
    result = run_on(official)
    bought = result.ledger.orders.filter(pl.col("date") == D[1]).select("code", "shares").rows()
    assert sorted(bought) == sorted(SHARES.items())
    forced = result.ledger.orders.filter(pl.col("date") == D[5])
    assert forced.select("code", "side", "shares", "price", "value", "reason").rows() == [
        (F, "sell", 25_000.0, 0.0, 0.0, "delist_write_down"),
        (M, "sell", 31_200.0, 8.0, 249_600.0, "delist_exit"),
    ]
    assert equity(result, D[4]) == pytest.approx(HELD, abs=1e-6)
    assert equity(result, D[5]) == pytest.approx(HELD - 250_000, abs=1e-6)  # F's 25,000 x 10.00 written off
    day5 = result.ledger.daily.filter(pl.col("date") == D[5]).row(0, named=True)
    assert day5["cash"] == pytest.approx(CASH + 249_600, abs=1e-6) and day5["sold"] == 249_600.0
    assert result.ledger.positions.filter(pl.col("date") == D[5])["code"].to_list() == [S]
    assert result.ledger.delisted.rows() == [
        (D[5], F, "write_down", 25_000.0, 250_000.0, 0.0),
        (D[5], M, "exit", 31_200.0, 249_600.0, 249_600.0),
    ]
    assert result.metrics["executable_order_share"].value == 1.0  # the three buys; the forced rows are not orders


def test_the_run_warns_of_the_cleared_holdings_and_the_suspension_held_past_twenty_days(official):
    """Design 2.4 delisted_holdings, and D17-5: stale_holdings covers the whole run, so the suspension is reported."""
    result = run_on(official)
    params = result.warnings["delisted_holdings"][1]
    assert params == {"codes": 2, "amount": 499_600.0, "max_share": pytest.approx(250_000 / HELD), "mergers": 1}
    stale = result.warnings["stale_holdings"][1]
    assert (stale["codes"], stale["worst_day"], stale["day"]) == (1, DAYS[23], DAYS[29])  # S, last bar on day 2
    assert stale["worst_share"] == pytest.approx(249_300 / (HELD - 250_000))
    assert "delisting_approximate" not in result.warnings


def test_without_official_dates_every_delisting_is_written_down_on_the_day_after_last_date(approximate):
    """v4 D17-3: before official dates, last_date's next trading day, a write-down to zero, delisting_approximate."""
    result = run_on(approximate)
    forced = result.ledger.orders.filter(pl.col("date") == D[3])
    assert forced.select("code", "price", "value", "reason").rows() == [
        (code, 0.0, 0.0, "delist_write_down") for code in (F, M, S)
    ]
    assert equity(result, D[3]) == pytest.approx(CASH, abs=1e-6)  # all three written off: only the cash is left
    params = result.warnings["delisting_approximate"][1]
    assert params == {"earlier_codes": 3, "maybe_suspended": 2}  # M and S are not evidently forced


Y, C = "600004.SH", "000002.SZ"


@pytest.fixture(scope="module")
def target_delisting(tmp_path_factory):
    """Y trades days 0 to 3 at 6.00 and delists on day 4; B and C trade every day at 20.00 and 30.00."""
    home, days = tmp_path_factory.mktemp("target"), DAYS[:6]
    bars = {Y: [(6.0, 6.0)] * 4 + [None] * 2, B: [(20.0, 20.0)] * 6, C: [(30.0, 30.0)] * 6}
    store = build_store(home / "home", days, bars)
    frame = store.read_table("securities").with_columns(
        last_date=pl.when(pl.col("code") == Y).then(pl.lit(D[3])).otherwise(pl.col("last_date")),
        name=pl.col("code").replace_strict({Y: "戊股份", B: "丁股份", C: "己股份"}),
        **{DELIST_DATE: pl.when(pl.col("code") == Y).then(pl.lit(D[4])).otherwise(pl.lit(None, pl.Date))},
        **{LIST_DATE: pl.lit(D[0])},
    )
    store.commit({}, replace={"securities": frame})
    return load_panel(store=store, cache_dir=home / "cache")


def test_a_target_delisting_the_day_it_is_bought_is_refused_no_bar_and_a_backup_stands_in(target_delisting):
    """Design 2.4: chosen at D-1 and delisted at D, the holding is cleared first, the buy refused no_bar and counted in
    executable_order_share, and the backup stands in."""
    cheapest = Strategy(factors=(Factor("price", Field("close"), prefer="low"),), top_n=1, backups=1, period="day")
    result = run(Backtest(cheapest, cash=100_000, execution=EX0, benchmark=None), target_delisting)
    columns = ("date", "code", "side", "requested", "shares", "price", "value", "commission", "transfer_fee")
    assert result.ledger.orders.select(*columns, "status", "reason").rows() == [
        (D[1], Y, "buy", 16_666.0, 16_600.0, 6.0, 99_600.0, 29.88, 1.0, "filled", None),
        (D[4], Y, "sell", 16_600.0, 16_600.0, 6.0, 99_600.0, 0.0, 0.0, "filled", "delist_exit"),  # cleared at the open
        (D[4], Y, "buy", 16_661.0, 0.0, None, 0.0, 0.0, 0.0, "rejected", "no_bar"),  # chosen on day 3, gone on day 4
        (D[4], B, "buy", 4_998.0, 4_900.0, 20.0, 98_000.0, 29.4, 0.98, "filled", None),  # the backup stands in
    ]
    assert 16_661 == int((100_000 - 99_600 - 29.88 - 1.0 + 99_600) / 6)  # the equity after the exit, at Y's last close
    assert result.metrics["executable_order_share"].value == pytest.approx(2 / 3)  # the refused buy counts
    detail = result.detail.filter((pl.col("date") == D[3]) & (pl.col("code") == Y))
    assert detail.select("held", "action").row(0) == (False, "buy")  # held at the close, cleared before the choice
