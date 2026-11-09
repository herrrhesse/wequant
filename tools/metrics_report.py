"""Run metrics of an index buy-and-hold, read from the store without writing to it.

Run: python -m tools.metrics_report [--home H] [--base 2005-01-04] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import polars as pl

from wequant.analytics import METRICS_VERSION, Metric, MetricSpec, compute_metrics, metrics_json
from wequant.data.store import Store
from wequant.version import code_version

HELD = "000300.SH"
BENCHMARKS = ("000905.SH", "000852.SH")

Runs = dict[str, dict[str, Metric]]  # benchmark code -> metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.metrics_report", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--base", type=dt.date.fromisoformat, default=dt.date(2005, 1, 4), help="day of the purchase")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    args = parser.parse_args(argv)
    text = report(Store(args.home), args.base)
    if args.out:
        args.out.write_text(text, "utf-8")
    else:
        sys.stdout.write(text)
    return 0


def report(store: Store, base: dt.date) -> str:
    """The Markdown report of buying HELD at base's close and holding it, against each of BENCHMARKS."""
    manifest = store.manifest()
    if manifest is None:
        raise SystemExit(f"{store.home}: nothing committed")
    calendar = store.scan("calendar", manifest=manifest).collect()["date"]
    daily = run_daily(store, manifest, calendar, base)
    spec = MetricSpec()
    runs = {code: compute_metrics(daily.with_columns(benchmark=pl.col(code)), calendar, spec) for code in BENCHMARKS}
    sections = (
        header(manifest["version"], daily, spec),
        metrics_section(runs),
        checks_section(daily, runs),
        json_section(runs, spec),
    )
    return "\n".join(line for section in sections for line in section) + "\n"


def run_daily(store: Store, manifest: dict, calendar: pl.Series, base: dt.date) -> pl.DataFrame:
    """Calendar days from base to HELD's last bar: HELD's close as equity, each benchmark's close (null without a bar)."""
    codes = [HELD, *BENCHMARKS]
    closes = (
        store.scan("index_bars", columns=["code", "date", "close"], codes=codes, start=base, manifest=manifest)
        .with_columns(pl.col("close") / 1000)
        .collect()
        .pivot(on="code", index="date", values="close")
    )
    last = closes.filter(pl.col(HELD).is_not_null())["date"].max()
    days = calendar.filter(calendar.is_between(base, last)).sort().to_frame()
    return days.join(closes, on="date", how="left").select("date", pl.col(HELD).alias("equity"), *BENCHMARKS)


def header(version: int, daily: pl.DataFrame, spec: MetricSpec) -> list[str]:
    settings = ", ".join(f"{key} {value}" for key, value in spec.to_dict().items())
    return [
        f"# Metrics of a {HELD} buy-and-hold",
        "",
        f"- **Store:** version {version}; code_version {code_version()}.",
        f"- **Run:** {HELD} bought at the close of {daily['date'][0]} and held to {daily['date'][-1]}; "
        "equity is its close in points.",
        f"- **Spec:** METRICS_VERSION {METRICS_VERSION}; {settings}.",
        "",
    ]


def metrics_section(runs: Runs) -> list[str]:
    first = runs[BENCHMARKS[0]]
    rows = [[key, metric.unit, *(cell(run[key]) for run in runs.values())] for key, metric in first.items()]
    return ["## 1. Metrics", "", *table(["metric", "unit", *(f"against {code}" for code in runs)], rows)]


def checks_section(daily: pl.DataFrame, runs: Runs) -> list[str]:
    metrics = runs[BENCHMARKS[0]]
    m = {key: metric.value for key, metric in metrics.items()}
    equity = dict(zip(daily["date"], daily["equity"]))
    first, last = daily["date"][0], daily["date"][-1]
    lines = [
        "## 2. Checks",
        "",
        f"- **Total return:** total_return {m['total_return']!r}; "
        f"close({last}) / close({first}) - 1 = {equity[last] / equity[first] - 1!r}.",
    ]
    peak, trough = m["max_drawdown_peak"], m["max_drawdown_trough"]
    if peak is None:
        lines.append("- **Max drawdown:** none.")
    else:
        lines.append(
            f"- **Max drawdown:** {m['max_drawdown']:.4f} from {peak} (close {equity[peak]:.3f}) to {trough} "
            f"(close {equity[trough]:.3f}); recovery {cell(metrics['max_drawdown_recovery'])}, "
            f"{m['max_drawdown_days']} days."
        )
    for code, run in runs.items():
        gaps = daily.filter(pl.col(code).is_null())["date"].to_list()
        lines.append(
            f"- **{code}:** days without a bar: {len(gaps)} ({', '.join(map(str, gaps)) or 'none'}); "
            f"benchmark_missing_days {cell(run['benchmark_missing_days'])}, since a missing close voids its own "
            "day's return and the next day's."
        )
    return [*lines, ""]


def json_section(runs: Runs, spec: MetricSpec) -> list[str]:
    text = metrics_json(runs[BENCHMARKS[0]], spec).decode().rstrip("\n")
    return [f"## 3. metrics.json against {BENCHMARKS[0]}", "", "```json", text, "```"]


def cell(metric: Metric) -> str:
    if metric.value is None:
        return f"null ({metric.reason})"
    if isinstance(metric.value, float):
        return f"{metric.value:.6f}"
    return str(metric.value)


def table(columns: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(" --- " for _ in columns) + "|"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows] + [""]


if __name__ == "__main__":
    raise SystemExit(main())
