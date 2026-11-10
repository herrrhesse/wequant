"""Backtest report on real data: no signal, buy-and-hold, speed, typical strategies, screen = backtest, progress.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default) and
the report.
Run: python -m tools.backtest_report [--home H] [--cache DIR] [--years 10] [--codes C1,C2,...] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import resource
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import numpy as np
import polars as pl

from tools.summary import Summary
from wequant.account import EXECUTION_VERSION, Execution
from wequant.analytics import METRICS_VERSION
from wequant.backtest import PROGRESS_DAYS, Backtest, Cancelled, Progress, panel_for, run, span
from wequant.data.files import parquet_bytes
from wequant.data.store import Store
from wequant.expr import BARSCOUNT, MA, REF, STD, Cache, Field
from wequant.panel import Panel, load_panel
from wequant.rules import limit_check_current
from wequant.runs.run import Run
from wequant.strategy import Condition, Factor, Group, Section, Strategy, decision_rows, screen, select

CODES = ("600000.SH", "000002.SZ", "600519.SH", "002594.SZ", "300750.SZ", "688981.SH")
CASH = 10_000_000.0  # yuan: the strategies of sections 1 and 3 to 6
HOLD_CASH = 1_000_000.0  # yuan: each buy-and-hold of section 2
EX0, EX10 = Execution(slippage_bps=0.0), Execution(slippage_bps=10.0)
REPEATS = 3
SPEED_LIMIT = 5.0  # seconds, best of REPEATS
PEAK_LIMIT = 5 * 1024**3  # bytes of peak RSS
TOLERANCE = 1e-6  # yuan between a buy-and-hold's equity and its hand-computed value
ADJ, AMOUNT, CLOSE = Field("adj_close"), Field("amount"), Field("close")
NEVER = Strategy(filters=(Condition("never", CLOSE < 0.0),))
ONE = Strategy(factors=(Factor("one", CLOSE),), top_n=1, period="day", every=10**6)
METRICS = ("total_return", "annual_return", "annual_volatility", "sharpe", "max_drawdown", "turnover")
METRICS += ("avg_holding_days", "executable_order_share", "tracking_error", "information_ratio")
LEDGER, DECISIONS = ("daily", "positions", "orders"), ("picks", "funnel", "detail")
HOLDINGS = ("held", "action", "sell_kind", "sell_step", "sell_rank", "sell_limit")  # detail columns a screen lacks
STATUSES = ("filled", "partial", "rejected")
FEES = ("commission", "stamp_tax", "transfer_fee", "slippage")


def strategy_m(anchor: dt.date | None = None, period: str = "week") -> Strategy:
    """Liquid, seasoned, tradable stocks ranked by momentum and low volatility: top 50 with 10 backups."""
    return Strategy(
        filters=(
            Condition("liquid", MA(AMOUNT, 20) > 1e8),
            Condition("seasoned", BARSCOUNT(CLOSE) > 60),
            Group("tradable", (Condition("no_st", ~Field("st")), Condition("not_up", ~Field("limit_up")))),
        ),
        factors=(Factor("momentum", ADJ / REF(ADJ, 20) - 1), Factor("low_vol", STD(ADJ / REF(ADJ, 1) - 1, 20), "low")),
        top_n=50,
        backups=10,
        period=period,
        anchor=anchor,
    )


def strategy_s(anchor: dt.date) -> Strategy:
    """Cheap liquid stocks after a fall, monthly, weighted by score with a 20% band."""
    return Strategy(
        filters=(Condition("liquid", MA(AMOUNT, 20) > 5e7), Condition("not_st", ~Field("st"))),
        factors=(Factor("cheap", CLOSE, prefer="low"), Factor("reversal", ADJ / REF(ADJ, 5), prefer="low")),
        top_n=30,
        period="month",
        weighting="score",
        band=0.2,
        anchor=anchor,
    )


@dataclass
class Report:
    """What every section reads: the store version, the cache, the window, the codes of section 2 and the summary."""

    store: Store
    cache: Path
    version: int
    years: int
    codes: list[str]
    dates: np.ndarray = field(init=False)  # the calendar of the store version
    start: dt.date = field(init=False)  # the first trading day on or after the last day less years
    shared: dict = field(default_factory=dict)  # results one section hands to a later one
    summary: Summary = field(default_factory=Summary)  # made before __post_init__ loads a panel: the start time

    def __post_init__(self) -> None:
        self.dates = self.panel().dates
        last = self.dates[-1].item()
        self.start = self.dates[np.searchsorted(self.dates, np.datetime64(years_before(last, self.years)))].item()

    def panel(self, start: dt.date | None = None, codes: list[str] | None = None) -> Panel:
        return load_panel(start, None, codes, version=self.version, store=self.store, cache_dir=self.cache)

    def panel_for(self, backtest: Backtest) -> Panel:
        return panel_for(backtest, store=self.store, version=self.version, cache_dir=self.cache)

    def m(self, anchor: dt.date | None, execution: Execution = EX0, period: str = "week") -> Backtest:
        return Backtest(strategy_m(anchor, period), start=self.start, cash=CASH, execution=execution)


class Timing(NamedTuple):
    seconds: float
    to_progress: float  # panel_for, benchmark and select: until the first progress call
    run: Run
    calls: list[Progress]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-backtest-"))
    report = Report(store, cache, manifest["version"], args.years, args.codes.split(","))
    text = render(report)
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        print(text, end="")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.backtest_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--years", type=int, default=10, help="years from the start to the last day")
    parser.add_argument("--codes", default=",".join(CODES), help="codes held in section 2")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    return parser.parse_args(argv)


def render(report: Report) -> str:
    """The Markdown report: the summary, the setup, every section, and the peak RSS taken after the last one."""
    load = os.getloadavg()
    body = [line for section in SECTIONS for line in section(report)]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    memory = report.summary.check("Memory: peak RSS", peak <= PEAK_LIMIT, f"{peak / 2**20:,.0f} MB (≤ 5 GB)")
    manifest = report.store.manifest(report.version)
    run_days = len(report.dates) - 1 - int(np.searchsorted(report.dates, np.datetime64(report.start)))
    setup = [
        "## Setup",
        "",
        f"- **Store:** {report.store.home}, last day {report.dates[-1]}",
        f"- **limit_check_current:** {limit_check_current(manifest)}",
        f"- **EXECUTION_VERSION:** {EXECUTION_VERSION}; **METRICS_VERSION:** {METRICS_VERSION}",
        f"- **Machine:** {os.cpu_count()} CPUs; load average at the start {fmt_load(load)}",
        f"- **Window:** start {report.start}, the first trading day on or after the last day less --years "
        f"{report.years}: {run_days:,} run days",
        f"- **Cache:** {report.cache}",
        "",
    ]
    footer = ["## Memory", "", f"- **Peak RSS:** {peak / 2**20:,.0f} MB: {memory} (≤ 5 GB)", ""]
    head = report.summary.head(f"Backtest report: store v{report.version}", report.version)
    return "\n".join(head + setup + body + footer) + "\n"


# ---------------------------------------------------------------- 1. no signal


def no_signal_section(report: Report) -> list[str]:
    backtest = Backtest(NEVER, start=report.start, cash=CASH, execution=EX0)
    result = run(backtest, report.panel_for(backtest))
    ledger = result.ledger
    flat = (ledger.daily["equity"] == CASH).all()
    ok = ledger.orders.is_empty() and flat and ledger.positions.is_empty() and "no_picks" in result.warnings
    measured = f"{ledger.orders.height:,} orders, {ledger.positions.height:,} positions, flat equity {flat}"
    return [
        "## 1. No signal",
        "",
        f"Strategy(filters=(Condition('never', close < 0.0),)) on the full panel from {report.dates[0]}, start "
        f"{report.start}, cash {CASH:,.0f}, {EX0}.",
        "",
        f"- **Orders:** {ledger.orders.height:,}; **positions:** {ledger.positions.height:,}; **equity equals the "
        f"cash on every day:** {flat}; **warnings:** {', '.join(result.warnings)}.",
        f"- **Result:** {report.summary.check('1. No signal', ok, measured)}",
        "",
    ]


# ---------------------------------------------------------------- 2. buy-and-hold


def hold_section(report: Report) -> list[str]:
    rows = [hold_row(report, code) for code in report.codes]
    ids = [row[-2] for row in rows]
    distinct = report.summary.check("2. Distinct run ids", len(set(ids)) == len(ids), f"{len(set(ids))} of {len(ids)}")
    return [
        "## 2. Buy-and-hold",
        "",
        f"One-code panels from {report.dates[0]}. The base is the first day from {report.start} on that trades, with "
        "a next day that trades inside known limits; the strategy buys on the next day with all of "
        f"{HOLD_CASH:,.0f} yuan ({EX0}) and holds. The final equity must equal cash - shares × price - fees + "
        "shares × price × (close × factor on the last traded day) / (open × factor on the buy day).",
        "",
        *table(["code", "base", "buy day", "status", "shares", "total_return", "equity gap", "id", ""], rows),
        f"- **Distinct run ids:** {len(set(ids))} of {len(ids)}: {distinct}",
        "",
    ]


def hold_row(report: Report, code: str) -> list:
    panel, name = report.panel(codes=[code]), f"2. Buy-and-hold {code}"
    base = hold_base(panel, int(np.searchsorted(panel.dates, np.datetime64(report.start))))
    if base is None:
        return [code, "no base day", *["—"] * 6, report.summary.check(name, False, "no base day")]
    result = run(Backtest(ONE, start=panel.dates[base].item(), cash=HOLD_CASH, execution=EX0, benchmark=None), panel)
    orders, last = result.ledger.orders, panel.dates[-1].item()
    if orders.height != 1 or orders["shares"][0] <= 0:
        found = f"{orders.height} orders"
        return [code, panel.dates[base], "—", found, "—", "—", "—", result.id, report.summary.check(name, False, found)]
    order = orders.row(0, named=True)
    gap = abs(result.ledger.daily["equity"][-1] - expected_end(panel, order, base + 1))
    held = result.ledger.positions.filter(pl.col("date") == last).height == 1
    cells = [order["status"], f"{order['shares']:,.0f}", f"{result.metrics['total_return'].value:.4f}", f"{gap:.2e}"]
    ok = report.summary.check(name, held and gap <= TOLERANCE, f"equity gap {gap:.2e} yuan")
    return [code, panel.dates[base], panel.dates[base + 1], *cells, result.id, ok]


def hold_base(panel: Panel, first: int) -> int | None:
    """The first row from first on that trades, whose next row trades and opens strictly inside known limits."""
    open_, up, down = panel.raw("open")[:, 0], panel.limit_up_price[:, 0], panel.limit_down_price[:, 0]
    traded, known = panel.traded[:, 0], panel.limit_known[:, 0]
    with np.errstate(invalid="ignore"):
        inside = (down[1:] < open_[1:]) & (open_[1:] < up[1:])
    rows = np.flatnonzero((traded[:-1] & traded[1:] & known[1:] & inside)[first:]) + first
    return int(rows[0]) if rows.size else None


def expected_end(panel: Panel, order: dict, buy: int) -> float:
    """The hand-computed final equity of one buy held to the end, dividends reinvested through the factors."""
    traded = panel.traded[:, 0]
    last = len(traded) - 1 - int(np.argmax(traded[::-1]))
    close, factor, open_ = panel.raw("close")[:, 0], panel.factor[:, 0], panel.raw("open")[:, 0]
    cost = order["shares"] * order["price"]
    growth = (close[last] * factor[last]) / (open_[buy] * factor[buy])
    return HOLD_CASH - cost - sum(order[name] for name in FEES) + cost * growth


# ---------------------------------------------------------------- 3. speed


def speed_section(report: Report) -> list[str]:
    timed(report, None)  # warm-up: memory maps and compiled operators
    ten = [timed(report, report.start) for _ in range(REPEATS)]
    load_ten = os.getloadavg()
    full = [timed(report, None) for _ in range(REPEATS)]
    load_full = os.getloadavg()
    report.shared["m"] = ten[-1]
    rows = [speed_row(report, "10-year anchor", ten, load_ten), speed_row(report, "default anchor", full, load_full)]
    counts = [
        count_row(f"{report.start} (10-year)", ten[-1].run),
        count_row(f"{report.dates[0]} (default)", full[-1].run),
    ]
    del full
    return [
        "## 3. Speed",
        "",
        f"M (weekly top 50 with 10 backups, all codes), start {report.start}, cash "
        f"{CASH:,.0f}, {EX0}. Each time is panel_for + run, whole, after one untimed warm-up; the split is the time to "
        "the first progress call (panel, benchmark and select), then the day loop, ledger and metrics. The best of "
        f"{REPEATS} must stay below {SPEED_LIMIT:.1f} s; a run is busy when the 1-minute load exceeds half the CPUs.",
        "",
        *table(["anchor", "times (s)", "median", "best", "to progress", "rest", "load", "result"], rows),
        *table(["anchor", "decisions", "orders", "filled", "partial", "rejected", "rejected by reason"], counts),
        *daily_lines(report),
    ]


def timed(report: Report, anchor: dt.date | None, period: str = "week") -> Timing:
    """M from anchor timed whole, with every progress call and the time to the first."""
    calls, marks = [], []

    def progress(state: Progress) -> None:
        marks.append(time.perf_counter())
        calls.append(state)

    started = time.perf_counter()
    backtest = report.m(anchor, period=period)
    result = run(backtest, report.panel_for(backtest), progress=progress)
    return Timing(time.perf_counter() - started, marks[0] - started, result, calls)


def speed_row(report: Report, label: str, timings: list[Timing], load: tuple[float, ...]) -> list:
    best = min(timings, key=lambda t: t.seconds)
    busy = load[0] > (os.cpu_count() or 1) / 2
    measured = f"{best.seconds:.2f} s, best of {REPEATS}" + ("; busy" if busy else "")
    result = report.summary.check(f"3. Speed, M weekly, {label}", best.seconds < SPEED_LIMIT, measured)
    result += " (busy)" if busy else ""
    times = ", ".join(f"{t.seconds:.2f}" for t in timings)
    median = statistics.median(t.seconds for t in timings)
    split = f"{best.to_progress:.2f}", f"{best.seconds - best.to_progress:.2f}"
    return [label, times, f"{median:.2f}", f"{best.seconds:.2f}", *split, fmt_load(load), result]


def count_row(label: str, result: Run) -> list:
    orders = result.ledger.orders
    status = dict(orders["status"].value_counts().iter_rows())
    reasons = orders.filter(pl.col("status") == "rejected")["reason"].value_counts().sort("reason")
    decisions = result.funnel["date"].n_unique()
    by_reason = ", ".join(f"{reason} {n:,}" for reason, n in reasons.iter_rows()) or "—"
    return [label, f"{decisions:,}", f"{orders.height:,}", *(f"{status.get(s, 0):,}" for s in STATUSES), by_reason]


def daily_lines(report: Report) -> list[str]:
    """Informational: M rebalanced daily at the 10-year anchor, its Section and its detail."""
    timing = timed(report, report.start, period="day")
    rows, size, seconds = timing.run.detail.height, len(parquet_bytes(timing.run.detail)), timing.seconds
    del timing
    backtest = report.m(report.start, period="day")
    panel = report.panel_for(backtest)
    section = select(backtest.strategy, panel, decision_rows(panel.dates, *span(backtest, panel), "day", 1))
    return [
        f"Informational, no verdict: M rebalanced daily at the 10-year anchor took {seconds:.2f} s; its Section holds "
        f"{section_bytes(section) / 1e6:,.0f} MB; its detail has {rows:,} rows and {size / 1e6:.1f} MB of Parquet.",
        "",
    ]


def section_bytes(section: Section) -> int:
    """Bytes of a Section's arrays, its dicts of grids included."""
    arrays = [a for part in section for a in (part.values() if isinstance(part, dict) else (part,))]
    return sum(a.nbytes for a in arrays if isinstance(a, np.ndarray))


# ---------------------------------------------------------------- 4. typical strategies


def typical_section(report: Report) -> list[str]:
    m10 = report.m(report.start, EX10)
    s = Backtest(strategy_s(report.start), start=report.start, cash=CASH, execution=EX0)
    runs = {
        "M, 0 bps": report.shared["m"].run,
        "M, 10 bps": run(m10, report.panel_for(m10)),
        "S, 0 bps": run(s, report.panel_for(s)),
    }
    lines = [
        "## 4. Typical strategies",
        "",
        f"M as in section 3 at the 10-year anchor; S = monthly top 30 of liquid non-ST stocks by cheapness and 5-day "
        f"reversal, score-weighted with a 20% band, anchored at {report.start}. Cash {CASH:,.0f}.",
        "",
        *table(["metric", *runs], [[name, *(metric(r, name) for r in runs.values())] for name in METRICS]),
    ]
    for label, result in runs.items():
        lines += strategy_lines(label, result)
    return lines


def strategy_lines(label: str, result: Run) -> list[str]:
    orders = result.ledger.orders
    counts = orders.group_by("status", "reason").len().sort("status", "reason").iter_rows()
    medians = result.funnel.group_by("step", maintain_order=True).agg(
        pl.col("passed").median(), pl.col("left").median()
    )
    sizes = {name: len(parquet_bytes(frame(result, name))) for name in LEDGER + DECISIONS}
    funnel = ", ".join(f"{step} {fmt(p)}/{fmt(left)}" for step, p, left in medians.iter_rows())
    return [
        f"### {label}",
        "",
        f"- **Orders:** {orders.height:,}: " + ", ".join(f"{s} ({r or 'in full'}) {n:,}" for s, r, n in counts),
        f"- **Picks:** {result.picks.height:,} rows on {result.funnel['date'].n_unique():,} decision days.",
        f"- **Funnel medians (passed/left):** {funnel}.",
        *[f"- **Warning {code}:** {text}." for code, text in result.warnings.items()],
        f"- **Detail rows:** {result.detail.height:,}; **Parquet bytes:** "
        + ", ".join(f"{name} {size / 1e6:.2f} MB" for name, size in sizes.items())
        + ".",
        "",
    ]


# ---------------------------------------------------------------- 5. screen equals backtest


def screen_section(report: Report) -> list[str]:
    result = report.shared["m"].run
    strategy = strategy_m(report.start)
    panel, cache = report.panel_for(report.m(report.start)), Cache()
    days = result.funnel["date"].unique(maintain_order=True)
    rows = []
    for day in (days[0], days[len(days) // 2], days[-1]):
        screened = screen(strategy, panel, day, cache)
        ran = {name: frame(result, name).filter(pl.col("date") == day) for name in DECISIONS}
        same = [without_holdings(getattr(screened, name)).equals(without_holdings(rows)) for name, rows in ran.items()]
        picks, detail = f"{ran['picks'].height:,}", f"{ran['detail'].height:,}"
        measured = f"{picks} picks rows, {detail} detail rows"
        ok = report.summary.check(f"5. Screen equals backtest on {day}", all(same), measured)
        rows.append([day, picks, detail, *same, ok])
    return [
        "## 5. Screen equals backtest",
        "",
        "screen(M, panel, day) against the run's rows of that day, on its first, middle and last decision days; the "
        "detail on the universe's rows, without the columns of the run's holdings.",
        "",
        *table(["day", "picks rows", "detail rows", "picks", "funnel", "detail", "result"], rows),
    ]


def without_holdings(frame: pl.DataFrame) -> pl.DataFrame:
    """A detail frame's universe rows without the columns of the holdings; any other frame as it is."""
    return frame.filter(pl.col("universe")).drop(HOLDINGS) if "held" in frame.columns else frame


# ---------------------------------------------------------------- 6. progress and cancel


def progress_section(report: Report) -> list[str]:
    timing = report.shared.pop("m")
    calls, total = timing.calls, timing.calls[-1].total
    want = 1 + math.ceil(total / PROGRESS_DAYS)
    late = cancelled(report, lambda calls, polls: calls >= 3)
    early = cancelled(report, lambda calls, polls: polls >= 2)
    rows = [
        [
            f"recording, {total:,} run days",
            f"{len(calls)} progress calls (want {want}), the last at done {calls[-1].done:,}",
            f"{timing.seconds:.2f}",
            report.summary.check(
                "6. Progress calls", len(calls) == want and calls[-1].done == total, f"{len(calls)} calls (want {want})"
            ),
        ],
        cancel_row(report, "cancel once 3 progress calls are made", late, late[0] and late[1] == 3),
        cancel_row(report, "cancel on its second poll, inside select", early, early[0] and early[1] == 0),
    ]
    return ["## 6. Progress and cancel", "", "M at the 10-year anchor.", "", *table(["run", "seen", "s", ""], rows)]


def cancelled(report: Report, stop: Callable[[int, int], bool]) -> tuple[bool, int, float]:
    """M at the 10-year anchor with cancel = stop(progress calls, polls so far): raised Cancelled, calls, seconds."""
    calls, polls = [], []

    def cancel() -> bool:
        polls.append(None)
        return stop(len(calls), len(polls))

    backtest, started = report.m(report.start), time.perf_counter()
    try:
        run(backtest, report.panel_for(backtest), progress=calls.append, cancel=cancel)
        raised = False
    except Cancelled:
        raised = True
    return raised, len(calls), time.perf_counter() - started


def cancel_row(report: Report, label: str, got: tuple[bool, int, float], ok: bool) -> list:
    raised, calls, seconds = got
    seen = f"{calls} progress calls, then {'Cancelled' if raised else 'no cancel'}"
    return [label, seen, f"{seconds:.2f}", report.summary.check(f"6. {label.capitalize()}", ok, seen)]


SECTIONS = (no_signal_section, hold_section, speed_section, typical_section, screen_section, progress_section)


# ---------------------------------------------------------------- helpers


def frame(result: Run, name: str) -> pl.DataFrame:
    return getattr(result.ledger, name) if name in LEDGER else getattr(result, name)


def metric(result: Run, name: str) -> str:
    m = result.metrics[name]
    return m.reason if m.value is None else f"{m.value:.4f}"


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def fmt_load(load: tuple[float, ...]) -> str:
    return " ".join(f"{x:.2f}" for x in load)


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def years_before(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


if __name__ == "__main__":
    raise SystemExit(main())
