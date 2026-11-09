"""Operator report on real data: panel shape, numba setup, MA20 timing, look-ahead checks and TDX values to compare.

Reads the store without the lock; writes only the panel cache (--cache, a fresh temporary directory by default), a
throwaway numba cache for the cold-import probe, and the report.
Run: python -m tools.ops_report [--home H] [--cache DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numba
import numpy as np

from wequant import ops
from wequant.data.store import Store
from wequant.panel import load_panel
from wequant.version import code_version

if TYPE_CHECKING:
    from wequant.panel import Panel

MA_LIMIT = 0.1  # seconds
TIMED_RUNS = 5
SLICE_ROWS = 500
CUTS = 5  # rows t checked per operator in the look-ahead section
COMPARED = ("600000.SH", "000001.SZ", "300750.SZ")
SHOWN_DAYS = 3
PROBE = """
import time
import numpy as np
started = time.perf_counter()
from wequant import ops
print(time.perf_counter() - started)
x = np.random.default_rng(0).random((250, 50))
for call in (lambda: ops.MA(x, 20), lambda: ops.CORR(x, x, 20), lambda: ops.CS_RANK(x)):
    started = time.perf_counter()
    call()
    print(time.perf_counter() - started)
"""
LOOK_AHEAD = {
    "MA(x, 20)": lambda x: ops.MA(x, 20),
    "EMA(x, 12)": lambda x: ops.EMA(x, 12),
    "HHV(x, 20)": lambda x: ops.HHV(x, 20),
    "REF(x, 5)": lambda x: ops.REF(x, 5),
    "BARSLAST(GT(x, REF(x, 1)))": lambda x: ops.BARSLAST(ops.GT(x, ops.REF(x, 1))),
    "CS_RANK(x)": ops.CS_RANK,
}


class Run:
    """One report run: a store version, its panel cache, and the panel of every SH/SZ stock."""

    def __init__(self, store: Store, version: int, cache: Path) -> None:
        self.store, self.version, self.cache = store, version, cache
        everything = self.panel()
        self.stocks = self.panel(everything.codes[everything.exchanges != "BJ"].tolist())
        self.bj = int((everything.exchanges == "BJ").sum())

    def panel(self, codes: list[str] | None = None) -> Panel:
        return load_panel(codes=codes, version=self.version, store=self.store, cache_dir=self.cache)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = Store(args.home)
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    cache = args.cache or Path(tempfile.mkdtemp(prefix="wequant-ops-"))
    text = report(Run(store, manifest["version"], cache))
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        print(text, end="")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.ops_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--cache", type=Path, help="panel cache directory (default: a fresh temporary directory)")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    return parser.parse_args(argv)


def report(run: Run) -> str:
    """The Markdown report: a header, then every section of SECTIONS."""
    manifest = run.store.manifest(run.version)
    lines = [
        f"# Operator report: store v{run.version}",
        "",
        f"- **Store:** {run.store.home} (version {run.version}, last trade date {manifest.get('last_trade_date')})",
        f"- **Panel key:** {run.stocks.key}",
        f"- **code_version:** {code_version()}",
        f"- **Cache:** {run.cache}",
        "",
    ]
    for section in SECTIONS:
        lines += section(run)
    return "\n".join(lines) + "\n"


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def seconds(call) -> float:
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def milliseconds(times) -> str:
    return ", ".join(f"{t * 1e3:.1f}" for t in times)


# ---------------------------------------------------------------- 1. panel and numba


def panel_section(run: Run) -> list[str]:
    close = run.stocks.raw("close")
    dates = run.stocks.dates
    ops.MA(close[:2, :2], 1)  # numba picks its threading layer on the first parallel call
    return [
        "## 1. Panel and numba",
        "",
        f"- **SH/SZ close panel:** {close.shape[0]:,} dates × {close.shape[1]:,} codes (BJ codes left out: {run.bj:,}), "
        f"{dates[0]} to {dates[-1]}; {np.isfinite(close).mean():.1%} of cells hold a bar.",
        f"- **numba:** {numba.__version__}, threading layer {numba.threading_layer()}, "
        f"{numba.get_num_threads()} threads.",
        "",
    ]


# ---------------------------------------------------------------- 2. import and first calls


def import_section(run: Run) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="wequant-numba-") as empty:
        cold = probe(empty)
    warm = probe()
    rows = [
        ["empty: every kernel and driver compiles", f"{cold[0]:.2f}", milliseconds(cold[1:])],
        ["warm", f"{warm[0]:.2f}", milliseconds(warm[1:])],
    ]
    return [
        "## 2. Import and first calls",
        "",
        "A fresh interpreter imports wequant.ops, then calls MA, CORR and CS_RANK once each on a 250 × 50 grid.",
        "Kernels and drivers have explicit signatures, so they compile or load from the cache at import; the first "
        "parallel call also starts numba's thread pool.",
        "",
        *table(["numba cache", "import (s)", "first MA, CORR, CS_RANK (ms)"], rows),
    ]


def probe(numba_cache: str | None = None) -> list[float]:
    """Seconds to import wequant.ops in a fresh interpreter, then for its first MA, CORR and CS_RANK calls."""
    env = dict(os.environ) if numba_cache is None else {**os.environ, "NUMBA_CACHE_DIR": numba_cache}
    done = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, check=True)
    return [float(line) for line in done.stdout.split()]


# ---------------------------------------------------------------- 3. MA20 timing


def timing_section(run: Run) -> list[str]:
    close = run.stocks.adjusted("close")
    rows = [timed_ma20(label, grid) for label, grid in (("C", close), ("F", np.asfortranarray(close)))]
    return [
        "## 3. MA20 on the full close panel",
        "",
        f"`MA(close, 20)` on the adjusted SH/SZ close, {close.shape[0]:,} × {close.shape[1]:,}, after one warm-up "
        f"call; {TIMED_RUNS} runs including the output allocation. Limit {MA_LIMIT * 1e3:.0f} ms.",
        "",
        *table(["layout", "runs (ms)", "median (ms)", "result"], rows),
    ]


def timed_ma20(label: str, grid: np.ndarray) -> list[str]:
    ops.MA(grid, 20)
    times = [seconds(lambda: ops.MA(grid, 20)) for _ in range(TIMED_RUNS)]
    median = statistics.median(times)
    return [label, milliseconds(times), f"{median * 1e3:.1f}", verdict(median < MA_LIMIT)]


# ---------------------------------------------------------------- 4. look-ahead


def look_ahead_section(run: Run) -> list[str]:
    x = run.stocks.adjusted("close")[-SLICE_ROWS:]
    cuts = np.unique(np.linspace(0, max(len(x) - 2, 0), CUTS).astype(int)).tolist()
    rng = np.random.default_rng(0)
    rows = []
    for name, op in LOOK_AHEAD.items():
        expected = op(x)
        prefix = all(same_bits(op(x[: t + 1]), expected[: t + 1]) for t in cuts)
        poison = all(same_bits(op(poisoned(x, t, rng))[: t + 1], expected[: t + 1]) for t in cuts)
        rows.append([f"`{name}`", verdict(prefix), verdict(poison)])
    return [
        "## 4. No look-ahead on real data",
        "",
        f"x = the last {len(x):,} rows of the adjusted close. Rows t = {', '.join(map(str, cuts))}:",
        "- **prefix:** op(x[:t+1]) equals op(x)[:t+1] bit for bit;",
        "- **poison:** shuffling, rescaling and punching gaps into the rows after t leaves rows up to t bit for bit.",
        "",
        *table(["operator", "prefix", "poison"], rows),
    ]


def poisoned(x: np.ndarray, t: int, rng: np.random.Generator) -> np.ndarray:
    """x with the rows after t shuffled, scaled by 1.1 and with one cell in ten made NaN."""
    later = rng.permutation(x[t + 1 :]) * 1.1
    later[rng.random(later.shape) < 0.1] = np.nan
    return np.concatenate([x[: t + 1], later])


def same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and a.tobytes() == b.tobytes()


# ---------------------------------------------------------------- 5. TDX indicators


def indicator_section(run: Run) -> list[str]:
    lines = [
        "## 5. TDX indicators to compare",
        "",
        "Front-adjusted prices (adjusted ÷ the code's factor on the last day; display only) over the whole panel, "
        "so EMA and SMA start at each code's first bar. MACD(12, 26, 9), KDJ(9, 3, 3), RSI(6), BOLL(20, 2).",
        "",
    ]
    present = set(run.stocks.codes.tolist())
    for code in COMPARED:
        lines += indicator_table(run.panel([code])) if code in present else [f"### {code}", "", "Not in the store.", ""]
    return lines


def indicator_table(panel: Panel) -> list[str]:
    """The last SHOWN_DAYS days of every indicator of a one-code panel."""
    close, high, low = (front_adjusted(panel, field)[:, 0] for field in ("close", "high", "low"))
    rows = [[name, *(number(v) for v in values[-SHOWN_DAYS:])] for name, values in indicators(close, high, low).items()]
    return [f"### {panel.codes[0]}", "", *table(["indicator", *map(str, panel.dates[-SHOWN_DAYS:])], rows)]


def front_adjusted(panel: Panel, field: str) -> np.ndarray:
    """Back-adjusted prices divided by each column's factor on the panel's last day."""
    return panel.adjusted(field) / panel.factor[-1]


def indicators(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict[str, np.ndarray]:
    """TDX indicators of one stock by their TDX names."""
    dif = ops.EMA(close, 12) - ops.EMA(close, 26)
    dea = ops.EMA(dif, 9)
    lowest = ops.LLV(low, 9)
    k = ops.SMA(ops.DIV(close - lowest, ops.HHV(high, 9) - lowest) * 100, 3, 1)
    d = ops.SMA(k, 3, 1)
    change = close - ops.REF(close, 1)
    mid, width = ops.MA(close, 20), 2 * ops.STD(close, 20)
    return {
        "CLOSE": close,
        **{f"MA{n}": ops.MA(close, n) for n in (5, 10, 20, 60)},
        "EMA12": ops.EMA(close, 12),
        "EMA26": ops.EMA(close, 26),
        "MACD.DIF": dif,
        "MACD.DEA": dea,
        "MACD.MACD": 2 * (dif - dea),
        "KDJ.K": k,
        "KDJ.D": d,
        "KDJ.J": 3 * k - 2 * d,
        "RSI.RSI1": ops.DIV(ops.SMA(ops.MAX(change, 0), 6, 1), ops.SMA(ops.ABS(change), 6, 1)) * 100,
        "BOLL.BOLL": mid,
        "BOLL.UB": mid + width,
        "BOLL.LB": mid - width,
    }


def number(value: float) -> str:
    return f"{value:.3f}" if np.isfinite(value) else "—"


SECTIONS = (panel_section, import_section, timing_section, look_ahead_section, indicator_section)


if __name__ == "__main__":
    raise SystemExit(main())
