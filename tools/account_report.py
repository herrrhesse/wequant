"""Account report on real data: buy-and-hold through ex-dates, a random order stream, speed and the data it trusts.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default) and
the report.
Run: python -m tools.account_report [--home H] [--cache DIR] [--codes C1,C2,...] [--start 2015-01-05] [--years 10]
     [--seed 20261109] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import resource
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from wequant.account import EXECUTION_VERSION, ORDER_REASONS, REJECTED, Account, Execution, Fills, Ledger
from wequant.analytics.inputs import known_fills
from wequant.analytics.metrics import capacity
from wequant.backtest.warnings import band_prices, main_band, st_band_fills
from wequant.data.store import Store
from wequant.panel import Panel, load_panel
from wequant.rules import default_book, limit_check_current, max_buy, max_sell, trade_fees
from wequant.version import code_version

CODES = ("600000.SH", "000002.SZ", "600519.SH", "002594.SZ", "300750.SZ", "688981.SH")
CASH = 1_000_000  # yuan per account in sections 1 and 2
STREAM_CODES = 300
STREAM_CHANCE = 0.3  # of a batch on a day
STREAM_EXECUTION = Execution(slippage_bps=5)
SPEED_CASH = 10_000_000
TOPS = (50, 200)
SPEED_LIMIT = 2.0  # seconds for the N = 50 loop
REBALANCE = 5  # rows t with t % REBALANCE == 1 rebalance
BLOCK = 500  # rows per block in section 4
STOPPED = 20  # rows before the end
MONEY = ("value", "commission", "stamp_tax", "transfer_fee", "slippage")
INVARIANTS = (
    "equity identity",
    "independent marks",
    "cash conservation",
    "cash on the fen grid, never negative",
    "share continuity",
    "buy at or above the up limit",
    "sell at or below the down limit",
    "fill without a bar or with an unknown limit",
    "T+1",
    "lots",
    "recomputed value and costs",
)


@dataclass
class Run:
    store: Store
    cache: Path
    version: int
    codes: list[str]
    start: dt.date
    years: int
    seed: int
    hits: set = field(default_factory=set)  # (code, date): a factor ratio below 1 scaled a holding of sections 1-2

    def panel(self, start=None, end=None, codes=None) -> Panel:
        return load_panel(start, end, codes, version=self.version, store=self.store, cache_dir=self.cache)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-account-"))
    run = Run(store, cache, manifest["version"], args.codes.split(","), args.start, args.years, args.seed)
    text = report(run)
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        print(text, end="")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.account_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--codes", default=",".join(CODES), help="codes held in section 1")
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2015, 1, 5), help="sections 1-2 start")
    parser.add_argument("--years", type=int, default=10, help="years in sections 3 and 4")
    parser.add_argument("--seed", type=int, default=20261109, help="seed of the random draws")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    return parser.parse_args(argv)


def report(run: Run) -> str:
    """The Markdown report: every section of SECTIONS, under a header written last for the peak RSS."""
    body = [line for section in SECTIONS for line in section(run)]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    manifest = run.store.manifest(run.version)
    header = [
        f"# Account report: store v{run.version}",
        "",
        f"- **Store:** {run.store.home} (version {run.version}, last trade date {manifest.get('last_trade_date')})",
        f"- **code_version:** {code_version()}",
        f"- **Rules key:** {run.panel().rules_key}",
        f"- **EXECUTION_VERSION:** {EXECUTION_VERSION}; sections 1 and 3 run {Execution()}: slippage is a cost and "
        "moves neither the fill price nor the limit checks.",
        "- **Corporate actions:** simulated with the adjustment factors, i.e. dividends reinvested; dividend tax is "
        "ignored.",
        f"- **Cache:** {run.cache}",
        f"- **Memory:** peak RSS: {peak:,.0f} MB, taken after section 4.",
        "",
    ]
    return "\n".join(header + body) + "\n"


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def li(yuan: float) -> int:
    return round(yuan * 1000)


def capacity_text(orders: pl.DataFrame) -> str:
    """The capacity metrics of a run's orders, with the fill behind the maximum."""
    m = {key: metric.value for key, metric in capacity(orders).items()}
    unknown = f"fills with an unknown day amount: {m['capacity_unknown_fills']:,}"
    if m["capacity_max"] is None:
        return f"no fill with a known day amount; {unknown}"
    fills, _ = known_fills(orders)
    top = fills.row(fills["day_share"].arg_max(), named=True)
    return (
        f"max {m['capacity_max']:.3%} ({top['code']} on {top['date']}: {top['value']:,.2f} of {top['day_amount']:,.2f} "
        f"yuan), 95th percentile {m['capacity_p95']:.3%}; {unknown}"
    )


def code_columns(panel: Panel, codes) -> np.ndarray:
    index = {code: j for j, code in enumerate(panel.codes.tolist())}
    return np.array([index[code] for code in codes], dtype=np.int64)


def day_rows(panel: Panel, days: pl.Series) -> np.ndarray:
    return np.searchsorted(panel.dates, days.to_numpy())


def years_before(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def down_hits(panel: Panel, ledger: Ledger) -> set:
    """(code, date) of each day a factor ratio below 1 scaled a position held at the previous close."""
    held = ledger.positions
    t, j = day_rows(panel, held["date"]) + 1, code_columns(panel, held["code"])
    keep = t < len(panel.dates)
    t, j = t[keep], j[keep]
    down = panel.factor[t, j] < panel.factor[t - 1, j]
    return set(zip(panel.codes[j[down]].tolist(), panel.dates[t[down]].tolist()))


def recomputed(panel: Panel, execution: Execution, t: int, j: int, side: str, got: float) -> tuple:
    """Value and the four costs in 厘 of a fill of got shares of column j on row t, from the rules."""
    if got == 0:
        return (0, 0, 0, 0, 0)
    price, whole = li(panel.raw(execution.price)[t, j]), math.floor(got)
    value = whole * price + math.floor((got - whole) * price / 10 + 0.5) * 10
    if whole == 0:
        return (value, 0, 0, 0, 0)
    fees = trade_fees(default_book(), panel.exchanges[j], panel.dates[t].item(), side, whole, price)
    cost = whole * price
    return value, execution.commission(cost), fees.stamp_tax, fees.transfer_fee, execution.slippage(cost)


# ---------------------------------------------------------------- 1. buy-and-hold


@dataclass
class Holding:
    ledger: Ledger
    refusals: Counter
    buy: int | None  # row of the buy fill
    shares: float
    cash: int  # 厘 after the buy
    sale: Fills | None
    execution: Execution


def hold_section(run: Run) -> list[str]:
    present = set(run.panel().codes.tolist())
    codes = [code for code in run.codes if code in present]
    panel = run.panel(run.start, codes=codes)
    holdings = [hold(panel, j) for j in range(len(codes))]
    for h in holdings:
        run.hits |= down_hits(panel, h.ledger)
    rows = [hold_row(panel, j, h) for j, h in enumerate(holdings)]
    missing = [code for code in run.codes if code not in present]
    header = ["code", "buy date", "refusals", "ex-dates held", "no-bar days", "final equity", "expected"]
    header += ["difference", "result", "share gap", "max rel. error", "result"]
    return [
        "## 1. Buy-and-hold through ex-dates",
        "",
        f"One account per code holds {CASH:,} yuan from {panel.dates[0]}. Each day until a buy fills it orders "
        "floor(cash / open) shares (1 share without a bar); it then holds and sells everything on "
        f"{panel.dates[-1]}. Expected: the cash after the buy plus the sale of q × r_1 × r_2 × … shares at the last "
        "open (r = factor[t] / factor[t-1] in row order, costs on whole shares, the fraction half up to the fen); "
        "when that sale does not fill (no bar or a limit that day, or a buy on that day), the cash after the buy plus "
        "those shares at the last traded close × factor ratio, with the reason. PASS at 0.001 yuan. The share gap is "
        "that product less q × factor_last / factor_buy (float representation, not judged). The value error compares "
        "each traded day's value with q × close × factor / factor_buy; PASS at 1e-12.",
        "",
        *table(header, rows),
        *([f"Not in the store: {', '.join(missing)}.", ""] if missing else []),
    ]


def hold(panel: Panel, j: int, execution: Execution = Execution()) -> Holding:
    """One account buys column j as soon as an order fills, holds it and sells all of it on the panel's last day."""
    account = Account(panel, CASH, panel.dates[0], execution)
    open_, last = panel.raw("open")[:, j], len(panel.dates) - 1
    refusals, buy, shares, cash, sale = Counter(), None, 0.0, 0, None
    for t in range(1, last + 1):
        account.begin(t)
        if buy is None:
            got = account.trade([j], [max(1, math.floor(account.cash / open_[t])) if open_[t] > 0 else 1])
            if got.status[0] == REJECTED:
                refusals[ORDER_REASONS[got.reason[0]]] += 1
            else:
                buy, shares, cash = t, float(got.shares[0]), li(account.cash)
        elif t == last:
            sale = account.trade([j], [-math.inf])
        account.end()
    return Holding(account.ledger(), refusals, buy, shares, cash, sale, execution)


def hold_row(panel: Panel, j: int, h: Holding) -> list:
    refusals = ", ".join(f"{reason} {n:,}" for reason, n in sorted(h.refusals.items())) or "none"
    if h.buy is None:
        return [panel.codes[j], "never", refusals, *["—"] * 9]
    factor, last = panel.factor[:, j], len(panel.dates) - 1
    held = slice(h.buy + 1, last + 1)
    shares = h.shares
    for t in range(h.buy + 1, last + 1):
        shares = shares * (factor[t] / factor[t - 1])
    final = h.ledger.daily["equity"][-1]
    expected, unsold = expected_equity(panel, j, h, shares)
    error = value_error(panel, j, h)
    return [
        panel.codes[j],
        panel.dates[h.buy],
        refusals,
        int((factor[held] != factor[h.buy : last]).sum()),
        int((~panel.traded[held, j]).sum()),
        f"{final:,.2f}",
        f"{expected:,.2f}" + (f" (held: {unsold})" if unsold else ""),
        f"{final - expected:.2f}",
        verdict(abs(final - expected) <= 0.001),
        f"{shares - h.shares * factor[last] / factor[h.buy]:.1e}",
        f"{error:.1e}",
        verdict(error <= 1e-12),
    ]


def expected_equity(panel: Panel, j: int, h: Holding, shares: float) -> tuple[float, str | None]:
    """Final equity with shares held after the buy, and why the last day's sale did not fill (None when it did)."""
    if h.sale is not None and h.sale.shares[0] > 0:
        return (h.cash + proceeds(panel, h.execution, j, shares)) / 1000, None
    unsold = "bought on the last day" if h.sale is None else ORDER_REASONS[h.sale.reason[0]]
    return h.cash / 1000 + shares * independent_marks(panel)[-1, j], unsold


def proceeds(panel: Panel, execution: Execution, j: int, shares: float) -> int:
    """厘 credited for selling shares of column j at the last open: value less costs."""
    value, *costs = recomputed(panel, execution, len(panel.dates) - 1, j, "sell", shares)
    return value - sum(costs)


def value_error(panel: Panel, j: int, h: Holding) -> float:
    """Largest relative error of the value of column j on traded held days against q × close × factor / factor_buy."""
    held = h.ledger.positions.filter(pl.col("code") == panel.codes[j])
    t = day_rows(panel, held["date"])
    traded = panel.traded[t, j]
    t, value = t[traded], held["value"].to_numpy()[traded]
    expected = h.shares * panel.raw("close")[t, j] * panel.factor[t, j] / panel.factor[h.buy, j]
    return float(np.max(np.abs(value - expected) / expected)) if t.size else 0.0


# ---------------------------------------------------------------- 2. random order stream


class Stream:
    """A random order stream through one account, counting each invariant's violations."""

    def __init__(self, panel: Panel, rng: np.random.Generator) -> None:
        self.panel, self.rng = panel, rng
        self.account = Account(panel, CASH, panel.dates[0], STREAM_EXECUTION)
        self.violations = Counter(dict.fromkeys(INVARIANTS, 0))
        self.book = default_book()

    def count(self, name: str, n) -> None:
        self.violations[name] += int(n)

    def run(self) -> Ledger:
        for t in range(1, len(self.panel.dates)):
            self.day(t)
        ledger = self.account.ledger()
        self.check_ledger(ledger)
        return ledger

    def day(self, t: int) -> None:
        account, factor = self.account, self.panel.factor
        closed = account.shares
        account.begin(t)
        self.count("equity identity", abs(account.cash + account.values.sum() - account.equity) > 1e-9 * account.equity)
        opened, bought, sold = account.sellable, np.zeros(closed.size), np.zeros(closed.size)
        calls = self.calls() if self.rng.random() < STREAM_CHANCE else []
        for cols, qty in calls:
            sellable = account.sellable.copy()
            fills = account.trade(cols, qty)
            for k in [k for k, q in enumerate(qty) if q < 0] + [k for k, q in enumerate(qty) if q > 0]:
                self.check_order(t, cols[k], qty[k] < 0, fills, k, sellable, bought, sold)
            self.count("cash on the fen grid, never negative", li(account.cash) % 10 != 0 or account.cash < 0)
        self.count("T+1", (sold > opened + 1e-9).sum())
        moved = closed * (factor[t] / factor[t - 1]) + bought - sold
        account.end()
        self.count("share continuity", (np.abs(account.shares - moved) > 1e-9 + 1e-12 * account.shares).sum())

    def calls(self) -> list[tuple[list[int], list[float]]]:
        """1..5 orders, buys of random codes and sells of held ones, in one call or two."""
        rng, held = self.rng, np.flatnonzero(self.account.shares > 0)
        orders = []
        for _ in range(rng.integers(1, 6)):
            if held.size and rng.random() < 0.5:
                orders.append(
                    (int(rng.choice(held)), -math.inf if rng.random() < 0.5 else -float(rng.integers(1, 3001)))
                )
            else:
                orders.append((int(rng.integers(len(self.panel.codes))), float(rng.integers(1, 3001))))
        cut = int(rng.integers(1, len(orders))) if len(orders) > 1 and rng.random() < 0.5 else len(orders)
        return [tuple(map(list, zip(*part))) for part in (orders[:cut], orders[cut:]) if part]

    def check_order(self, t: int, j: int, sell: bool, fills: Fills, k: int, sellable, bought, sold) -> None:
        got, side = fills.shares[k], "sell" if sell else "buy"
        money = tuple(li(getattr(fills, name)[k]) for name in MONEY)
        self.count("recomputed value and costs", money != recomputed(self.panel, STREAM_EXECUTION, t, j, side, got))
        if not got:
            return
        self.check_fill(t, j, sell, got, fills.price[k], sellable[j])
        if sell:
            sellable[j] = 0.0 if got == sellable[j] else sellable[j] - got
            sold[j] += got
        else:
            bought[j] += got

    def check_fill(self, t: int, j: int, sell: bool, got: float, price: float, sellable: float) -> None:
        panel = self.panel
        up, down = panel.limit_up_price[t, j], panel.limit_down_price[t, j]
        self.count("fill without a bar or with an unknown limit", not (panel.traded[t, j] and panel.limit_known[t, j]))
        self.count("buy at or above the up limit", not sell and price >= up)
        self.count("sell at or below the down limit", sell and price <= down)
        lot = self.book.in_force("lot", panel.boards[j], panel.dates[t].item())
        valid = (got == sellable or max_sell(lot, got, sellable) == got) if sell else max_buy(lot, int(got)) == got
        self.count("lots", not valid)

    def check_ledger(self, ledger: Ledger) -> None:
        panel, daily, held = self.panel, ledger.daily, ledger.positions
        values = held.group_by("date").agg(pl.col("value").sum())
        marked = daily.join(values, on="date", how="left").with_columns(pl.col("value").fill_null(0.0))
        gap = (marked["equity"] - marked["cash"] - marked["value"]).abs()
        self.count("equity identity", (gap > 1e-9 * marked["equity"]).sum())
        t, j = day_rows(panel, held["date"]), code_columns(panel, held["code"])
        expected = held["shares"].to_numpy() * independent_marks(panel)[t, j]
        self.count("independent marks", (np.abs(held["value"].to_numpy() - expected) > 1e-12 * expected).sum())
        orders = ledger.orders
        flows = {name: (orders[name] * 1000).round().cast(pl.Int64) for name in MONEY}
        costs = flows["commission"] + flows["stamp_tax"] + flows["transfer_fee"] + flows["slippage"]
        sign = pl.Series(np.where(orders["side"].to_numpy() == "sell", 1, -1))
        self.count("cash conservation", li(CASH) + int((sign * flows["value"] - costs).sum()) != li(daily["cash"][-1]))
        off_grid = (daily["cash"] * 1000).round().cast(pl.Int64) % 10 != 0
        self.count("cash on the fen grid, never negative", (off_grid | (daily["cash"] < 0)).sum())


def independent_marks(panel: Panel) -> np.ndarray:
    """(T, N) the last traded raw close in each row's adjustment, from the panel alone."""
    t = np.arange(len(panel.dates))[:, None]
    last = np.maximum.accumulate(np.where(panel.traded, t, 0), axis=0)
    close, factor = (np.take_along_axis(grid, last, axis=0) for grid in (panel.raw("close"), panel.factor))
    return close * (factor / panel.factor)


def stream_section(run: Run) -> list[str]:
    everything = run.panel()
    rng = np.random.default_rng(run.seed)
    pool = everything.codes[everything.exchanges != "BJ"]
    codes = np.sort(rng.choice(pool, min(STREAM_CODES, pool.size), replace=False)).tolist()
    panel = run.panel(run.start, codes=codes)
    stream = Stream(panel, rng)
    ledger = stream.run()
    run.hits |= down_hits(panel, ledger)
    orders = ledger.orders
    statuses = orders["status"].value_counts(sort=True).iter_rows()
    rows = [[name, f"{n:,}", verdict(n == 0)] for name, n in stream.violations.items()]
    near = (ledger.positions["shares"] - ledger.positions["shares"].round()).abs()
    st_buys, st_sells = st_band_fills(panel, orders)
    return [
        "## 2. Random order stream",
        "",
        f"{len(codes)} SH/SZ codes drawn with seed {run.seed}, {panel.dates[0]} to {panel.dates[-1]}, cash {CASH:,}, "
        f"Execution(slippage_bps={STREAM_EXECUTION.slippage_bps:g}). On each day, with probability {STREAM_CHANCE}, "
        "1..5 orders (buys of random codes for 1..3000 shares; sells of held codes for 1..3000 shares or the whole "
        "position) in one or two calls.",
        "",
        f"- **Orders:** {orders.height:,} ({', '.join(f'{s} {n:,}' for s, n in statuses)}).",
        f"- **Final equity:** {ledger.daily['equity'][-1]:,.2f}.",
        "",
        *table(["invariant", "violations", "result"], rows),
        "Informational:",
        f"- **Capacity:** each fill's value over its stock's traded amount that day: {capacity_text(orders)}.",
        "- **ST band:** fills on LIMITED main-board cells with st_known False on days of a narrower ST band, at "
        f"its price from the day's preclose: buys at the +ST-band price {st_buys:,}, sells at the -ST-band price "
        f"{st_sells:,}.",
        f"- **Near-whole holdings:** {int(((near > 0) & (near <= 1e-6)).sum()):,} position rows within 1e-6 of a whole "
        "number of shares but not whole (D3 float noise; holdings are not snapped).",
        "",
    ]


# ---------------------------------------------------------------- 3. speed


def speed_section(run: Run) -> list[str]:
    last = run.panel().dates[-1].item()
    panel = run.panel(years_before(last, run.years))
    rows, capacities = [], []
    for top in TOPS:
        orders, loop, seconds, ledger = speed_run(panel, top, np.random.default_rng([run.seed, top]))
        limit = f"{verdict(loop < SPEED_LIMIT)} (limit {SPEED_LIMIT:.1f} s)" if top == TOPS[0] else "—"
        rows.append([top, f"{orders:,}", f"{loop:.2f}", f"{seconds:.2f}", f"{loop / max(orders, 1) * 1e6:.1f}", limit])
        capacities.append(f"- **Capacity, N = {top}:** {capacity_text(ledger.orders)}.")
    return [
        "## 3. Speed",
        "",
        f"The full panel from {panel.dates[0]} ({len(panel.dates):,} rows × {len(panel.codes):,} codes, memory-map "
        f"views), cash {SPEED_CASH:,}. Every row t with t % {REBALANCE} == 1 targets the top N of seeded random "
        "scores over the SH/SZ codes traded that day: one call sells every held code outside the target, a second "
        "buys each new target with budget = cash / new targets at the fill price, by descending score.",
        "",
        *table(["N", "orders", "loop (s)", "ledger (s)", "µs per order", "result"], rows),
        "Each fill's value over its stock's traded amount that day:",
        "",
        *capacities,
        "",
    ]


def speed_run(panel: Panel, top: int, rng: np.random.Generator) -> tuple[int, float, float, Ledger]:
    """Orders, loop seconds, ledger seconds and the ledger of one rebalancing run."""
    account, price = Account(panel, SPEED_CASH, panel.dates[0]), panel.raw("open")
    eligible, orders = panel.traded & (panel.exchanges != "BJ"), 0
    started = time.perf_counter()
    for t in range(1, len(panel.dates)):
        account.begin(t)
        if t % REBALANCE == 1:
            orders += rebalance(account, price[t], np.flatnonzero(eligible[t]), top, rng)
        account.end()
    loop = time.perf_counter() - started
    started = time.perf_counter()
    ledger = account.ledger()
    return orders, loop, time.perf_counter() - started, ledger


def rebalance(account: Account, price: np.ndarray, pool: np.ndarray, top: int, rng: np.random.Generator) -> int:
    """Sell held codes outside the top of random scores over pool, then buy the new ones; the number of orders."""
    target = pool[np.argsort(-rng.random(pool.size), kind="stable")[:top]]
    held = np.flatnonzero(account.shares > 0)
    sells, buys = np.setdiff1d(held, target), target[~np.isin(target, held)]
    if sells.size:
        account.trade(sells, np.full(sells.size, -np.inf))
    if buys.size:
        account.trade(buys, np.maximum(1, np.floor(account.cash / buys.size / price[buys])))
    return sells.size + buys.size


# ---------------------------------------------------------------- 4. data the account relies on

MEASURES = {
    "outside": "LIMITED SH/SZ bars with high above the up limit or low below the down limit",
    "outside_ex": "… of them on an ex-date (a factor change on that row)",
    "unknown": "traded SH/SZ cells with an unknown limit",
    "open_up": "SH/SZ days that open at the up limit",
    "close_up": "SH/SZ days that close at the up limit",
    "st_up": "main-board days at the +ST-band price all day (open = high = close), opening below the normal up limit",
    "st_down": "main-board days at the -ST-band price all day (open = high = low), opening above the normal down limit",
}


def data_section(run: Run) -> list[str]:
    everything = run.panel()
    dates, stock = everything.dates, everything.exchanges != "BJ"
    since = int(np.searchsorted(dates, np.datetime64(years_before(dates[-1].item(), run.years), "D")))
    counts, last_traded = data_counts(run, dates, stock)
    rows = [[text, f"{int(counts[k].sum()):,}", f"{int(counts[k][since:].sum()):,}"] for k, text in MEASURES.items()]
    stopped = stock & (last_traded >= 0) & (last_traded < len(dates) - 1 - STOPPED)
    text = f"SH/SZ codes that stop trading more than {STOPPED} rows before the end (held at their last close)"
    rows.append([text, f"{int(stopped.sum()):,}", f"{int((stopped & (last_traded >= since)).sum()):,}"])
    current = limit_check_current(run.store.manifest(run.version))
    return [
        "## 4. Data the account relies on",
        "",
        f"- **limit_check_current:** {current}; the account validates orders against the limits as they are.",
        "- ST-band rows count main-board SH/SZ LIMITED cells with st_known False on days whose band has st_bp below "
        "normal_bp; the ST-band price comes from the day's preclose with the band's st_bp and the lot tick.",
        "",
        *table(["measure", f"whole store (from {dates[0]})", f"last {run.years} years (from {dates[since]})"], rows),
        *factor_steps(run, dates),
    ]


def data_counts(run: Run, dates: np.ndarray, stock: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Per-row counts of every MEASURES key in blocks of BLOCK rows, and each code's last traded row (-1 if never)."""
    counts = {key: np.zeros(len(dates), dtype=np.int64) for key in MEASURES}
    band, last_traded = main_band(dates), np.full(stock.size, -1)
    for lo in range(0, len(dates), BLOCK):
        first, hi = max(lo - 1, 0), min(lo + BLOCK, len(dates))
        panel = run.panel(dates[first], dates[hi - 1])
        skip = lo - first  # the row before the block only gives the factor step into its first row
        for key, per_row in block_counts(panel, stock, tuple(b[first:hi] for b in band)).items():
            counts[key][lo:hi] = per_row[skip:]
        traded_rows = np.where(panel.traded[skip:], np.arange(lo, hi)[:, None], -1).max(axis=0)
        last_traded = np.maximum(last_traded, traded_rows)
    return counts, last_traded


def block_counts(panel: Panel, stock: np.ndarray, band) -> dict[str, np.ndarray]:
    """Per row of a block panel, the cell counts of MEASURES over SH/SZ codes."""
    up, down = panel.limit_up_price, panel.limit_down_price
    limited = np.isfinite(up) & stock
    step = np.vstack([np.zeros((1, up.shape[1]), dtype=bool), panel.factor[1:] != panel.factor[:-1]])
    outside = limited & ((panel.raw("high") > up) | (panel.raw("low") < down))
    plus, minus = st_locked(panel, limited & (panel.boards == "main") & ~panel.st_known, band)
    return {
        "outside": outside.sum(axis=1),
        "outside_ex": (outside & step).sum(axis=1),
        "unknown": (panel.traded & np.isnan(up) & stock).sum(axis=1),
        "open_up": (limited & (panel.raw("open") == up)).sum(axis=1),
        "close_up": (limited & (panel.raw("close") == up)).sum(axis=1),
        "st_up": plus,
        "st_down": minus,
    }


def st_locked(panel: Panel, cells: np.ndarray, band) -> tuple[np.ndarray, np.ndarray]:
    """Per row, cells locked all day at the +ST-band and at the -ST-band price, on days the ST band is narrower."""
    st_bp, normal_bp, tick = band
    t, j = np.nonzero(cells & (st_bp < normal_bp)[:, None])
    o, h, low, c = (np.rint(panel.raw(f)[t, j] * 1000) for f in ("open", "high", "low", "close"))
    up, down = band_prices(np.rint(panel.raw("preclose")[t, j] * 1000), st_bp[t], tick[t])
    plus = (o == h) & (h == c) & (c == up) & (o < np.rint(panel.limit_up_price[t, j] * 1000))
    minus = (o == h) & (h == low) & (low == down) & (o > np.rint(panel.limit_down_price[t, j] * 1000))
    size = len(panel.dates)
    return np.bincount(t[plus], minlength=size), np.bincount(t[minus], minlength=size)


def factor_steps(run: Run, dates: np.ndarray) -> list[str]:
    """adj_factor steps after each code's first row: those below 1, the holdings they scaled and the smallest."""
    adj = run.store.scan("adj_factor", manifest=run.store.manifest(run.version)).sort("code", "ex_date").collect()
    steps = adj.with_columns(ratio=pl.col("factor") / pl.col("factor").shift().over("code")).drop_nulls("ratio")
    down = steps.filter(pl.col("ratio") < 1).sort("ratio")
    days = dates[np.minimum(np.searchsorted(dates, down["ex_date"].to_numpy()), len(dates) - 1)].tolist()
    hits = sum((code, day) in run.hits for code, day in zip(down["code"].to_list(), days))
    smallest = [
        [code, day, f"{ratio:.4f}"] for code, day, ratio in down.head(10).select("code", "ex_date", "ratio").iter_rows()
    ]
    return [
        f"- **adj_factor steps below 1:** {down.height:,} of {steps.height:,} steps after each code's first row, "
        f"{down.filter(pl.col('ratio') < 0.99).height:,} of them below 0.99; {hits:,} scaled a holding of sections "
        "1-2 (D3 shrinks the position by the ratio)." + ("" if smallest else " None to list."),
        "",
        *(table(["code", "ex_date", "ratio"], smallest) if smallest else []),
    ]


SECTIONS = (hold_section, stream_section, speed_section, data_section)


if __name__ == "__main__":
    raise SystemExit(main())
