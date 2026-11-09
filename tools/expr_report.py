"""Expression report on real data: panel, nine expressions against direct ops calls, cache, look-ahead, rejections.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default) and the
report.
Run: python -m tools.expr_report [--home H] [--cache DIR] [--years 10] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import resource
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from wequant import ops
from wequant.data.codes import EXCHANGES
from wequant.data.store import Store
from wequant.expr import (
    ABS,
    AND,
    BARSCOUNT,
    BARSLAST,
    COUNT,
    CROSS,
    CS_RANK,
    EMA,
    EXPR_VERSION,
    HHV,
    LLV,
    MA,
    MAX,
    REF,
    SMA,
    STD,
    Cache,
    Expr,
    ExprError,
    Field,
    LookAheadError,
    Op,
    evaluate,
    from_form,
)
from wequant.panel import Panel, load_panel
from wequant.version import code_version

CLOSE, VOLUME, AMOUNT, OPEN = Field("close"), Field("volume"), Field("amount"), Field("open")
ADJ_CLOSE, ADJ_HIGH, ADJ_LOW = Field("adj_close"), Field("adj_high"), Field("adj_low")
ST, LIMIT_UP = Field("st"), Field("limit_up")
CUTS = (0.4, 0.7)  # shares of the rows; the second-to-last row is the third cut


def macd_cross() -> Expr:
    dif = EMA(ADJ_CLOSE, 12) - EMA(ADJ_CLOSE, 26)
    return CROSS(dif, EMA(dif, 9))


def kdj_j_oversold() -> Expr:
    lowest = LLV(ADJ_LOW, 9)
    k = SMA((ADJ_CLOSE - lowest) / (HHV(ADJ_HIGH, 9) - lowest) * 100, 3, 1)
    return 3 * k - 2 * SMA(k, 3, 1) < 0


def rsi6_low() -> Expr:
    change = ADJ_CLOSE - REF(ADJ_CLOSE, 1)
    return SMA(MAX(change, 0), 6, 1) / SMA(ABS(change), 6, 1) * 100 < 20


EXPRESSIONS: dict[str, Expr] = {
    "ma_cross": CROSS(MA(ADJ_CLOSE, 5), MA(ADJ_CLOSE, 20)),
    "momentum_rank": CS_RANK(ADJ_CLOSE / REF(ADJ_CLOSE, 20) - 1),
    "volume_breakout": (VOLUME > 2 * MA(VOLUME, 20)) & (ADJ_CLOSE >= HHV(ADJ_CLOSE, 20)),
    "near_high": ADJ_CLOSE / HHV(ADJ_HIGH, 250) > 0.9,
    "tradable_filter": ~ST & ~LIMIT_UP & (MA(AMOUNT, 20) > 1e8) & (BARSCOUNT(CLOSE) > 60),
    "low_vol": CS_RANK(-STD(ADJ_CLOSE / REF(ADJ_CLOSE, 1) - 1, 20)),
    "macd_cross": macd_cross(),
    "kdj_j_oversold": kdj_j_oversold(),
    "rsi6_low": rsi6_low(),
}
LOOK_AHEAD: dict[str, Expr] = {
    "macd_cross": EXPRESSIONS["macd_cross"],
    "momentum_rank": EXPRESSIONS["momentum_rank"],
    "BARSLAST(limit_up)": BARSLAST(LIMIT_UP),
    "~limit_up & (close < 50)": ~LIMIT_UP & (CLOSE < 50),
}
REJECTED: list[tuple[str, type[ExprError], Callable[[], Expr]]] = [
    ("REF(adj_close, -1)", LookAheadError, lambda: REF(ADJ_CLOSE, -1)),
    ("Op('ZIG', adj_close, 5)", LookAheadError, lambda: Op("ZIG", ADJ_CLOSE, 5)),
    ("Field('suspended')", LookAheadError, lambda: Field("suspended")),
    ("MA(adj_close, volume)", ExprError, lambda: MA(ADJ_CLOSE, VOLUME)),
    ("MA(adj_close, 0)", ExprError, lambda: MA(ADJ_CLOSE, 0)),
    ("COUNT(adj_close, 5)", ExprError, lambda: COUNT(ADJ_CLOSE, 5)),
    ("AND(adj_close, open)", ExprError, lambda: AND(ADJ_CLOSE, OPEN)),
]


# ---------------------------------------------------------------- the same expressions as nested wequant.ops calls


def direct_ma_cross(p: Panel) -> np.ndarray:
    c = p.adjusted("close")
    return ops.CROSS(ops.MA(c, 5), ops.MA(c, 20))


def direct_momentum_rank(p: Panel) -> np.ndarray:
    c = p.adjusted("close")
    return ops.CS_RANK(ops.SUB(ops.DIV(c, ops.REF(c, 20)), 1.0))


def direct_volume_breakout(p: Panel) -> np.ndarray:
    v, c = p.raw("volume"), p.adjusted("close")
    return ops.AND(ops.GT(v, ops.MUL(2.0, ops.MA(v, 20))), ops.GE(c, ops.HHV(c, 20)))


def direct_near_high(p: Panel) -> np.ndarray:
    return ops.GT(ops.DIV(p.adjusted("close"), ops.HHV(p.adjusted("high"), 250)), 0.9)


def direct_tradable_filter(p: Panel) -> np.ndarray:
    tradable = ops.AND(ops.NOT(p.on_bars(p.st)), ops.NOT(p.on_bars(p.limit_up)))
    liquid = ops.GT(ops.MA(p.raw("amount"), 20), 1e8)
    return ops.AND(ops.AND(tradable, liquid), ops.GT(ops.BARSCOUNT(p.raw("close")), 60.0))


def direct_low_vol(p: Panel) -> np.ndarray:
    c = p.adjusted("close")
    return ops.CS_RANK(ops.SUB(0.0, ops.STD(ops.SUB(ops.DIV(c, ops.REF(c, 1)), 1.0), 20)))


def direct_macd_cross(p: Panel) -> np.ndarray:
    c = p.adjusted("close")
    dif = ops.SUB(ops.EMA(c, 12), ops.EMA(c, 26))
    return ops.CROSS(dif, ops.EMA(dif, 9))


def direct_kdj_j_oversold(p: Panel) -> np.ndarray:
    lowest = ops.LLV(p.adjusted("low"), 9)
    rsv = ops.DIV(ops.SUB(p.adjusted("close"), lowest), ops.SUB(ops.HHV(p.adjusted("high"), 9), lowest))
    k = ops.SMA(ops.MUL(rsv, 100.0), 3, 1)
    return ops.LT(ops.SUB(ops.MUL(3.0, k), ops.MUL(2.0, ops.SMA(k, 3, 1))), 0.0)


def direct_rsi6_low(p: Panel) -> np.ndarray:
    c = p.adjusted("close")
    change = ops.SUB(c, ops.REF(c, 1))
    rsi = ops.MUL(ops.DIV(ops.SMA(ops.MAX(change, 0.0), 6, 1), ops.SMA(ops.ABS(change), 6, 1)), 100.0)
    return ops.LT(rsi, 20.0)


DIRECT: dict[str, Callable[[Panel], np.ndarray]] = {
    "ma_cross": direct_ma_cross,
    "momentum_rank": direct_momentum_rank,
    "volume_breakout": direct_volume_breakout,
    "near_high": direct_near_high,
    "tradable_filter": direct_tradable_filter,
    "low_vol": direct_low_vol,
    "macd_cross": direct_macd_cross,
    "kdj_j_oversold": direct_kdj_j_oversold,
    "rsi6_low": direct_rsi6_low,
}


# ---------------------------------------------------------------- the run


class Run:
    """One report run: a store version, its panel cache and the panel of every code over the last years."""

    def __init__(self, store: Store, version: int, cache: Path, years: int) -> None:
        self.store, self.version, self.cache = store, version, cache
        last = self.load().dates[-1].item()
        self.panel = self.load(start=years_before(last, years), end=last)
        self.panel.limit_up_price  # build a missing limit array before any field is held
        self.kept: dict[str, np.ndarray] = {}  # full results of the look-ahead expressions

    def load(self, **query) -> Panel:
        return load_panel(version=self.version, store=self.store, cache_dir=self.cache, **query)


def years_before(day: dt.date, years: int) -> dt.date:
    """The day after the same date years before day (28 February for 29 February)."""
    try:
        earlier = day.replace(year=day.year - years)
    except ValueError:
        earlier = day.replace(year=day.year - years, day=28)
    return earlier + dt.timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-expr-"))
    text = report(Run(store, manifest["version"], cache, args.years))
    if args.out:
        args.out.write_text(text, "utf-8")
        print(f"{args.out}: {peak_rss_mb()} MB peak RSS")
    else:
        print(text, end="")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.expr_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--years", type=int, default=10, help="years of data up to the last trading day")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    return parser.parse_args(argv)


def report(run: Run) -> str:
    """The Markdown report: every section of SECTIONS, then a header that records the peak RSS they reached."""
    body = [line for section in SECTIONS for line in section(run)]
    panel = run.panel
    header = [
        f"# Expression report: store v{panel.version}",
        "",
        f"- **Store:** {run.store.home} (version {panel.version}); code_version {code_version()}",
        f"- **Panel key:** {panel.key}; **rules key:** {panel.rules_key}; **EXPR_VERSION:** {EXPR_VERSION}",
        f"- **Cache:** {run.cache}",
        f"- **Memory:** peak RSS: {peak_rss_mb():,} MB",
        "",
    ]
    return "\n".join(header + body) + "\n"


def peak_rss_mb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def timed(call: Callable[[], np.ndarray]) -> tuple[np.ndarray, float]:
    """call's result and the milliseconds it took."""
    started = time.perf_counter()
    result = call()
    return result, (time.perf_counter() - started) * 1e3


def same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


def nodes(e: Expr) -> list[Expr]:
    """The distinct nodes of a tree: what one evaluation computes."""
    seen, stack = {}, [e]
    while stack:
        node = stack.pop()
        if node.key not in seen:
            seen[node.key] = node
            if isinstance(node, Op):
                stack.extend(a for a in node.args if isinstance(a, Expr))
    return list(seen.values())


# ---------------------------------------------------------------- 1. panel


def panel_section(run: Run) -> list[str]:
    p = run.panel
    exchanges = ", ".join(f"{x} {int((p.exchanges == x).sum()):,}" for x in EXCHANGES)
    return [
        "## 1. Panel",
        "",
        f"- **Shape:** {len(p.dates):,} dates × {len(p.codes):,} codes, {p.dates[0]} to {p.dates[-1]}.",
        f"- **Codes by exchange:** {exchanges}. BJ codes stay in, so the rankings cover every code with a value.",
        f"- **Bars:** {p.traded.mean():.1%} of cells hold one.",
        "",
    ]


# ---------------------------------------------------------------- 2. expressions


def expressions_section(run: Run) -> list[str]:
    rows, colds = zip(*(expression_row(run, name, e) for name, e in EXPRESSIONS.items()))
    return [
        "## 2. Expressions",
        "",
        "Each expression is evaluated with a fresh Cache (cold), again with the same Cache (warm), and as the same "
        "nested wequant.ops calls written by hand (direct); equal compares the cold and direct results bit for bit. "
        "Its fields are evaluated once before the timings, so the grids the panel memoizes (adjusted prices, masks) "
        "count in neither cold nor direct.",
        "",
        *table(["expression", "nodes", "distinct", "cold (ms)", "warm (ms)", "direct (ms)", "equal"], rows),
        f"Cold total: {sum(colds) / 1e3:.2f} s.",
        "",
    ]


def expression_row(run: Run, name: str, e: Expr) -> tuple[list, float]:
    """One expression's table row and its cold milliseconds."""
    tree = nodes(e)
    for field in [node for node in tree if isinstance(node, Field)]:
        evaluate(field, run.panel)  # whichever timing ran first would pay for the grids the panel memoizes
    cache = Cache()
    result, cold = timed(lambda: evaluate(e, run.panel, cache))
    _, warm = timed(lambda: evaluate(e, run.panel, cache))
    expected, direct = timed(lambda: DIRECT[name](run.panel))
    if name in LOOK_AHEAD:
        run.kept[name] = result
    equal = verdict(same_bits(result, expected))
    return [f"`{name}`", e.size, len(tree), f"{cold:.1f}", f"{warm:.3f}", f"{direct:.1f}", equal], cold


# ---------------------------------------------------------------- 3. cache


def cache_section(run: Run) -> list[str]:
    cache = Cache()
    same = True
    for e in EXPRESSIONS.values():
        first = evaluate(e, run.panel, cache)
        repeats = (evaluate(e, run.panel, cache), evaluate(from_form(json.loads(e.key)), run.panel, cache))
        same &= all(r is first for r in repeats)
    ok = cache.hits == 2 * len(EXPRESSIONS) and cache.misses == len(EXPRESSIONS) and same
    row = [cache.hits, cache.misses, f"{cache.nbytes / 2**20:,.0f}", verdict(ok)]
    return [
        "## 3. Cache",
        "",
        "One fresh Cache: each expression is evaluated twice, then once more rebuilt from its canonical form; "
        "the repeats must return the very grids of the first evaluation.",
        "",
        *table(["hits", "misses", "MB held", "result"], [row]),
    ]


# ---------------------------------------------------------------- 4. no look-ahead


def look_ahead_section(run: Run) -> list[str]:
    rows_total = len(run.panel.dates)
    cuts = [int(share * rows_total) for share in CUTS] + [rows_total - 2]
    full = {name: run.kept.pop(name) if name in run.kept else evaluate(e, run.panel) for name, e in LOOK_AHEAD.items()}
    verdicts = {name: [] for name in LOOK_AHEAD}
    for cut in cuts:
        prefix = run.load(start=run.panel.dates[0], end=run.panel.dates[cut])
        for name, e in LOOK_AHEAD.items():
            verdicts[name].append(verdict(same_bits(evaluate(e, prefix), full[name][: cut + 1])))
        del prefix
    header = ["expression", *(f"row {cut:,} ({run.panel.dates[cut]})" for cut in cuts)]
    return [
        "## 4. No look-ahead",
        "",
        "Each expression evaluated on a panel that ends at a cut row equals the full panel's rows up to that cut, "
        "bit for bit.",
        "",
        *table(header, [[f"`{name}`", *cells] for name, cells in verdicts.items()]),
    ]


# ---------------------------------------------------------------- 5. rejected


def rejected_section(run: Run) -> list[str]:
    return [
        "## 5. Rejected",
        "",
        "Each of these must raise the expected error when it is built.",
        "",
        *table(["expression", "expected", "raised", "message", "result"], [rejected_row(*case) for case in REJECTED]),
    ]


def rejected_row(text: str, expected: type[ExprError], build: Callable[[], Expr]) -> list[str]:
    try:
        build()
    except ExprError as error:
        raised, message = type(error), str(error)
    else:
        raised, message = None, "built"
    name = raised.__name__ if raised else "nothing"
    return [f"`{text}`", expected.__name__, name, message, verdict(raised is expected)]


SECTIONS = (panel_section, expressions_section, cache_section, look_ahead_section, rejected_section)


if __name__ == "__main__":
    raise SystemExit(main())
