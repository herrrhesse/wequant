"""Checks that the data follows the conventions the conversion relies on.

Each check returns (stats, problems). Stats feed the report; any problem stops the import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.data import codes as codemod

if TYPE_CHECKING:
    from tools.investment_data.qlib import Panel
    from tools.investment_data.adjustment import Ratio
    from tools.investment_data.convert import Recovered, Stocks

UNIT_YEAR_MIN = 0.995  # share of a year's stock rows that must pass the unit check
RESIDUAL_FEN = 0.2  # price residuals above this are listed
YOUNG_DAYS = 5  # dropped rows this close to a stock's first day are listed for review

Result = tuple[dict, list[str]]


def run_all(
    panel: Panel,
    rec: Recovered,
    stocks: Stocks,
    ratio: Ratio,
    preclose: np.ndarray,
    tables: dict[str, pl.DataFrame],
    invalid_members: list[tuple],
    unmapped_lists: list[str],
) -> tuple[dict, list[str]]:
    results = {
        "layout": layout(panel),
        "fields": fields(panel),
        "kinds": kinds(rec),
        "first_close": first_close(rec),
        "rounding": rounding(rec),
        "units": units(rec),
        "duplicates": duplicate_indices(rec),
        "adjustment": adjustment(stocks, ratio, preclose, rec.codes),
        "ohlc": ohlc(tables),
        "coverage": coverage(rec, tables, invalid_members, unmapped_lists),
        "issues": data_issues(tables["data_issues"]),
    }
    stats = {name: s for name, (s, _) in results.items()}
    stats["last_trade_date"] = str(rec.dates.max())
    stats["table_rows"] = {name: df.height for name, df in tables.items()}
    return stats, [p for _, problems in results.values() for p in problems]


def layout(panel: Panel) -> Result:
    problems = [f"{len(panel.issues)} instruments have feature files that do not match their span"] if panel.issues else []
    return {"issues": panel.issues}, problems


def fields(panel: Panel) -> Result:
    """Coverage per field, and the NaN pattern: a day either has every field or none (besides factor and change)."""
    v = panel.values
    finite = {f: np.isfinite(x) for f, x in v.items()}
    has_data = finite["close"]
    rows = np.flatnonzero(has_data)
    first_with_data = np.zeros(has_data.size, bool)
    first_with_data[rows[np.r_[True, panel.code_idx[rows[1:]] != panel.code_idx[rows[:-1]]]]] = True
    core = [f for f in v if f not in ("factor", "change")]
    anomalies = {
        "rows with data but a NaN field": (has_data & ~np.logical_and.reduce([finite[f] for f in [*core, "factor"]])).sum(),
        "rows with data and NaN change after the first day": (has_data & ~finite["change"] & ~first_with_data).sum(),
        "rows without data but a finite field": (~has_data & np.logical_or.reduce([finite[f] for f in [*core, "change"]])).sum(),
        "rows with data and volume <= 0": (has_data & (np.nan_to_num(v["volume"], nan=1) <= 0)).sum(),
        "infinite values": sum(np.isinf(x).sum() for x in v.values()),
    }
    anomalies = {name: int(count) for name, count in anomalies.items()}
    stats = {
        "rows": int(has_data.size),
        "rows_with_data": int(has_data.sum()),
        "coverage": {f: (int(ok.sum()), float(ok.mean())) for f, ok in finite.items()},
        "anomalies": anomalies,
        "factor_filled_rows": int((~has_data & finite["factor"]).sum()),
    }
    return stats, [f"{count} {name}" for name, count in anomalies.items() if count]


def kinds(rec: Recovered) -> Result:
    counts = {k: int((rec.kinds == k).sum()) for k in ("stock", "index", "fund", "unknown")}
    eras = [codemod.bse_code_era(c) for c, k in zip(rec.codes, rec.kinds) if k == "stock" and c.endswith(".BJ")]
    unknown = [c for c, k in zip(rec.codes, rec.kinds) if k == "unknown"]
    stats = {
        "counts": counts,
        "indices": [c for c, k in zip(rec.codes, rec.kinds) if k == "index"],
        "bse": {"old": eras.count("old"), "new": eras.count("new")},
    }
    return stats, [f"{len(unknown)} codes match no classification rule: {unknown[:10]}"] if unknown else []


def first_close(rec: Recovered) -> Result:
    """Upstream divides every series by its first adjusted close, so the first stored close is 1."""
    first = rec.stored_close[np.r_[True, rec.code_idx[1:] != rec.code_idx[:-1]]]
    stats = {
        "instruments": int(first.size),
        "within_1e-6": float(np.mean(np.abs(first - 1) < 1e-6)),
        "within_1e-3": float(np.mean(np.abs(first - 1) < 1e-3)),
        "range": (float(first.min()), float(first.max())),
    }
    return stats, []


def rounding(rec: Recovered) -> Result:
    """How far recovered stock prices are from whole fen."""
    stock = rec.kind == "stock"
    stats: dict = {"fields": {}}
    for f in ("open", "high", "low", "close"):
        fen = np.abs(rec.price[f][stock] - np.round(rec.price[f][stock], 2)) * 100
        stats["fields"][f] = {"max": float(fen.max()), "p99.99": float(np.quantile(fen, 0.9999)), "over": int((fen > RESIDUAL_FEN).sum())}
    fen = np.abs(rec.price["close"] - np.round(rec.price["close"], 2)) * 100
    worst = np.flatnonzero(stock & (fen > RESIDUAL_FEN))
    worst = worst[np.argsort(-fen[worst])][:20]
    stats["worst"] = [(rec.codes[rec.code_idx[k]], str(rec.dates[k]), float(rec.price["close"][k]), float(fen[k])) for k in worst]
    return stats, []


def units(rec: Recovered) -> Result:
    """amount (千元) / volume (手) must be a price inside the day's range."""
    stock = rec.kind == "stock"
    price = rec.amount_k * 1000 / (rec.lots * 100)
    inside = (price >= rec.price["low"] * 0.99) & (price <= rec.price["high"] * 1.01)
    years = rec.dates.astype("datetime64[Y]").astype(int) + 1970
    problems = []
    by_year = {}
    for y in np.unique(years[stock]).tolist():
        rows = stock & (years == y)
        by_year[y] = (int(rows.sum()), float(inside[rows].mean()))
        if by_year[y][1] < UNIT_YEAR_MIN:
            problems.append(f"unit check {y}: only {by_year[y][1]:.4f} of stock rows pass")

    scale = price / rec.price["close"]
    by_code: dict[int, list[int]] = {}
    for k in np.flatnonzero(off_scale(rec)).tolist():
        by_code.setdefault(int(rec.code_idx[k]), []).append(k)
    dropped = [
        (rec.codes[c], len(ks), int((rec.code_idx == c).sum()), str(rec.dates[ks[0]]), str(rec.dates[ks[-1]]), round(float(np.median(scale[ks])), 3))
        for c, ks in sorted(by_code.items(), key=lambda item: -len(item[1]))
    ]

    index = rec.kind == "index"
    stats = {
        "by_year": by_year,
        "dropped": dropped,
        "dropped_rows": sum(n for _, n, *_ in dropped),
        "vwap_max_rel": float(np.max(np.abs(rec.price["vwap"] / price - 1))),
        "index_share_price": (float(price[index].min()), float(price[index].max())) if index.any() else None,
        "index_points": (float(rec.price["close"][index].min()), float(rec.price["close"][index].max())) if index.any() else None,
    }
    return stats, problems


def off_scale(rec: Recovered) -> np.ndarray:
    """Stock rows whose amount / volume is not even near the close: volume or amount is off by a unit."""
    scale = rec.amount_k * 1000 / (rec.lots * 100) / rec.price["close"]
    return (rec.kind == "stock") & ((scale < 0.5) | (scale > 2))


def duplicate_indices(rec: Recovered) -> Result:
    indices = np.flatnonzero(rec.kinds == "index")
    series = {i: rec.code_idx == i for i in indices}
    pairs = []
    for n, a in enumerate(indices):
        for b in indices[n + 1 :]:
            ra, rb = series[a], series[b]
            if np.array_equal(rec.dates[ra], rec.dates[rb]) and all(
                np.array_equal(x[ra], x[rb]) for x in (rec.stored_close, rec.lots, rec.amount_k)
            ):
                pairs.append((rec.codes[a], rec.codes[b]))
    return {"pairs": pairs}, []


def adjustment(stocks: Stocks, ratio: Ratio, preclose: np.ndarray, codes: list[str]) -> Result:
    ci, dates = stocks.code_idx, stocks.dates
    same = ci[1:] == ci[:-1]
    daily = stocks.adjclose.astype(np.float64) / stocks.close
    daily_step = np.abs(daily[1:] / daily[:-1] - 1)[same]

    run_code = ci[ratio.run_starts]
    run_cont = np.flatnonzero(np.r_[False, run_code[1:] == run_code[:-1]])
    run_step = np.abs(ratio.run_values[run_cont] / ratio.run_values[run_cont - 1] - 1)

    event_code = ci[ratio.event_rows]
    event_ratio = ratio.event_ratio
    cont = np.flatnonzero(np.r_[False, event_code[1:] == event_code[:-1]])
    step = event_ratio[cont] / event_ratio[cont - 1] - 1

    def where(row: int) -> tuple[str, str]:
        return codes[ci[row]], str(dates[row])

    big_drops = cont[step < -0.01][np.argsort(step[step < -0.01])]
    years = dates.astype("datetime64[Y]").astype(int)
    code_years = np.unique(ci.astype(np.int64) * 10_000 + years)
    events = ci[ratio.event_rows[cont]].astype(np.int64) * 10_000 + years[ratio.event_rows[cont]]
    per_code_year = np.searchsorted(code_years, events)
    counts = np.bincount(per_code_year, minlength=code_years.size)

    following = np.r_[False, (ratio.row_ratio[1:] == ratio.row_ratio[:-1]) & same]
    stats = {
        "daily_steps": int(daily_step.size),
        "daily_over_1e-5": int((daily_step > 1e-5).sum()),
        "daily_1e-6_to_1e-4": int(((daily_step >= 1e-6) & (daily_step <= 1e-4)).sum()),
        "runs": int(ratio.run_starts.size),
        "breaks": int(run_cont.size),
        "breaks_1e-6_to_1e-4": int(((run_step >= 1e-6) & (run_step <= 1e-4)).sum()),
        "merged": int(ratio.merged_runs.size),
        "blips": [
            (*where(ratio.run_starts[k]), float(stocks.close[ratio.run_starts[k]]), round(float(stocks.adjclose[ratio.run_starts[k]]), 2),
             float(ratio.run_values[k - 1]), float(ratio.run_values[k]), float(ratio.run_values[k + 1]))
            for k in ratio.blip_runs
        ],
        "rows": int(ratio.event_rows.size),
        "events": int(cont.size),
        "event_sizes": np.histogram(np.abs(step), [1e-4, 1e-3, 1e-2, 1e-1, np.inf])[0].tolist(),
        "negative_events": int((step < 0).sum()),
        "big_drops": [(*where(ratio.event_rows[k]), float(event_ratio[k] / event_ratio[k - 1] - 1), float(event_ratio[k - 1]), float(event_ratio[k])) for k in big_drops],
        "events_per_code_year": dict(zip(*[x.tolist() for x in np.unique(counts, return_counts=True)])),
        "preclose_mismatch": int((preclose[following] != stocks.close[np.flatnonzero(following) - 1]).sum()),
    }
    return stats, []


def ohlc(tables: dict[str, pl.DataFrame]) -> Result:
    rules = {
        "low > open": pl.col("low") > pl.col("open"),
        "low > close": pl.col("low") > pl.col("close"),
        "open > high": pl.col("open") > pl.col("high"),
        "close > high": pl.col("close") > pl.col("high"),
        "price <= 0": pl.min_horizontal("open", "high", "low", "close") <= 0,
        "volume < 0": pl.col("volume") < 0,
    }
    stats = {}
    for name in ("bars", "index_bars"):
        df = tables[name]
        stats[name] = {
            "counts": {rule: int(df.select(expr.sum()).item()) for rule, expr in rules.items()},
            "sample": df.filter(pl.any_horizontal(list(rules.values())))
            .select("code", "date", "open", "high", "low", "close", "volume")
            .head(10)
            .rows(),
        }
    return stats, []


def coverage(rec: Recovered, tables: dict[str, pl.DataFrame], invalid_members: list[tuple], unmapped_lists: list[str]) -> Result:
    last = rec.dates.max()
    cutoff = last - np.timedelta64(30, "D")
    stocks = [(c, s) for c, s, k in zip(rec.codes, rec.spans, rec.kinds) if k == "stock"]
    members = tables["index_members"]
    all_codes = set(rec.codes)
    stats = {
        "by_year": tables["bars"]
        .group_by(pl.col("date").dt.year().alias("year"))
        .agg(stocks=pl.col("code").n_unique(), rows=pl.len())
        .sort("year")
        .rows(),
        "stale_stocks": sum(s.end < cutoff for _, s in stocks),
        "stale_indices": [(c, str(s.end)) for c, s, k in zip(rec.codes, rec.spans, rec.kinds) if k == "index" and s.end < cutoff],
        "calendar": (int(rec.calendar.size), str(rec.calendar[0]), tables["calendar"].height, str(last)),
        "members": dict(members.group_by("index_code").len().sort("index_code").iter_rows()),
        "members_not_listed": int((~members["code"].is_in(list(all_codes))).sum()),
        "dropped_members": invalid_members,
        "removed_stocks": sorted({c for c, _ in stocks} - set(tables["securities"]["code"])),
    }
    return stats, [f"instrument lists with no known index code: {unmapped_lists}"] if unmapped_lists else []


def data_issues(issues: pl.DataFrame) -> Result:
    """Counts by kind, and the dropped rows from a stock's first days, when no price limit applies yet."""
    measured = issues.filter(pl.col("kind") == "off_scale").with_columns(
        days=pl.col("detail").str.json_path_match("$.days_since_first").cast(pl.Int64),
        scale=pl.col("detail").str.json_path_match("$.scale").cast(pl.Float64),
    )
    young = measured.filter(pl.col("days") < YOUNG_DAYS).sort("code", "date")
    stats = {
        "by_kind": issues.group_by("kind", "action").len().sort("kind").rows(),
        "young_off_scale": young.select("code", pl.col("date").cast(pl.String), "scale", "days").rows(),
    }
    return stats, []
