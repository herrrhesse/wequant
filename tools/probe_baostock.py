"""Probe BaoStock: speed and limits, historical names, coverage, agreement with the seed store, backfill cost.

Writes $WEQUANT_HOME/reports/baostock_probe_<date>.md and a .samples.json of raw responses.
Reads the store, never writes to it. Every query runs without retries, so errors are recorded as they happen.

Run: uv run --extra baostock python -m tools.probe_baostock
"""

from __future__ import annotations

import datetime as dt
import json
import multiprocessing
import random
import time

import numpy as np
import polars as pl

from wequant.data.sources.baostock import BaoStock, BaoStockError, from_baostock, to_baostock
from wequant.data.store import Store

BUDGET = 5000
PARALLEL = (2, 4)


class BudgetExhausted(RuntimeError):
    pass


class Recording(BaoStock):
    """BaoStock without retries that enforces the request budget and keeps a sample of each method's raw response."""

    def __init__(self, budget: int):
        super().__init__(retries=0)
        self.budget = budget
        self.samples: dict[str, dict] = {}

    def query(self, method, **params):
        if self.requests >= self.budget:
            raise BudgetExhausted(f"{self.requests} requests used")
        fields, rows = super().query(method, **params)
        self.samples.setdefault(method, {"params": params, "fields": fields, "rows": rows[:5]})
        return fields, rows


def timed(call):
    started = time.perf_counter()
    value = call()
    return value, time.perf_counter() - started


def quantiles(values: list[float]) -> dict:
    a = np.array(values)
    return (
        {"n": int(a.size), "median": float(np.median(a)), "p95": float(np.quantile(a, 0.95)), "max": float(a.max())}
        if a.size
        else {"n": 0}
    )


# ---------------------------------------------------------------- 2.1 speed and limits


def _worker(tasks: list[tuple[str, int]]) -> dict:
    source = BaoStock(retries=0)
    out = {"latencies": [], "rows": 0, "errors": [], "requests": 0}
    try:
        source.login()
        for code, year in tasks:
            try:
                bars, seconds = timed(lambda: source.daily_bars(code, f"{year}-01-01", f"{year}-12-31"))
            except BaoStockError as error:
                out["errors"].append(str(error))
                break
            out["latencies"].append(seconds)
            out["rows"] += bars.height
        source.logout()
    except BaoStockError as error:
        out["errors"].append(str(error))
    out["requests"] = source.requests
    return out


def speed(source: Recording, tasks: list[tuple[str, int]], full_history_code: str) -> dict:
    result: dict = {}
    latencies, rows, errors = [], 0, []
    single, wall = tasks[:200], time.perf_counter()
    for code, year in single:
        try:
            bars, seconds = timed(lambda: source.daily_bars(code, f"{year}-01-01", f"{year}-12-31"))
        except BaoStockError as error:
            errors.append(str(error))
            break
        latencies.append(seconds)
        rows += bars.height
    wall = time.perf_counter() - wall
    result["single"] = {
        **quantiles(latencies),
        "rows": rows,
        "rows_per_s": rows / wall,
        "calls_per_s": len(latencies) / wall,
        "errors": errors,
    }

    before = source.requests
    history, seconds = timed(lambda: source.daily_bars(full_history_code, "1990-12-19", dt.date.today().isoformat()))
    result["full_history"] = {
        "code": full_history_code,
        "rows": history.height,
        "first": str(history["date"].min()),
        "seconds": seconds,
        "requests": source.requests - before,
    }

    result["parallel"] = {}
    offset = 200
    ctx = multiprocessing.get_context("spawn")
    for n in PARALLEL:
        chunks = [tasks[offset + i * 100 : offset + (i + 1) * 100] for i in range(n)]
        offset += 100 * n
        with ctx.Pool(n) as pool:
            outs, wall = timed(lambda: pool.map(_worker, chunks))
        calls = sum(len(o["latencies"]) for o in outs)
        result["parallel"][n] = {
            **quantiles([s for o in outs for s in o["latencies"]]),
            "calls": calls,
            "rows": sum(o["rows"] for o in outs),
            "wall": wall,
            "calls_per_s": calls / wall,
            "rows_per_s": sum(o["rows"] for o in outs) / wall,
            "errors": [e for o in outs for e in o["errors"]],
            "requests": sum(o["requests"] for o in outs),
        }
        if result["parallel"][n]["errors"]:
            break
    return result


def snapshots(source: Recording, days: list[str]) -> dict:
    """The per-day cross-section queries added in baostock 0.9: all stocks' bars / adjust factors for one date."""
    out = {}
    for method in ("query_daily_history_k_AStock", "query_daily_adjust_factor"):
        out[method] = []
        for day in days:
            try:
                (fields, rows), seconds = timed(lambda: source.query(method, date=day))
            except BaoStockError as error:
                out[method].append({"day": day, "error": str(error)})
                continue
            entry = {"day": day, "rows": len(rows), "seconds": seconds, "fields": fields}
            if "tradestatus" in fields and rows:
                entry["suspended"] = sum(r[fields.index("tradestatus")] == "0" for r in rows)
            if "isST" in fields and rows:
                entry["st"] = sum(r[fields.index("isST")] == "1" for r in rows)
            if "code" in fields and rows:
                entry["exchanges"] = sorted({r[fields.index("code")][:2] for r in rows})
            out[method].append(entry)
    return out


# ---------------------------------------------------------------- 2.2 historical names


def st_names(source: Recording, latest: str, wanted: int = 6) -> dict:
    today = source.securities_on(latest)
    current = dict(today.select("code", "name").iter_rows())
    candidates = sorted(c for c, n in current.items() if "ST" in (n or "") and c.endswith((".SH", ".SZ")))
    random.Random(1).shuffle(candidates)
    changes, scanned = [], 0
    for code in candidates[:40]:
        scanned += 1
        fields, rows = source.query(
            "query_history_k_data_plus",
            code=to_baostock(code),
            fields="date,isST",
            start_date="2016-01-01",
            end_date=latest,
            frequency="d",
            adjustflag="3",
        )
        flags = [(r[0], r[1]) for r in rows if r[1] in ("0", "1")]
        flips = [i for i in range(1, len(flags)) if flags[i][1] != flags[i - 1][1]]
        if flips:
            i = flips[-1]
            around = [flags[j] for j in (i - 2, i - 1, i, i + 1) if 0 <= j < len(flags)]
            changes.append({"code": code, "flips": [flags[j][0] for j in flips], "around": around})
        if len(changes) >= wanted:
            break
    checks, names_by_day = [], {}
    for change in changes:
        for day, flag in change["around"]:
            if day not in names_by_day:
                names_by_day[day] = dict(source.securities_on(day).select("code", "name").iter_rows())
            name = names_by_day[day].get(change["code"])
            checks.append((change["code"], day, flag, name, current.get(change["code"])))
    named = [c for c in checks if c[3] is not None]
    historical = sum(("ST" in c[3]) == (c[2] == "1") for c in named)
    always_current = sum(c[3] == c[4] for c in named)
    if named and historical == len(named) and always_current < len(named):
        verdict = "historical: the name is the one in force on that day"
    elif named and always_current == len(named):
        verdict = "always the current name"
    else:
        verdict = "other"
    return {
        "latest": latest,
        "scanned": scanned,
        "changes": changes,
        "checks": checks,
        "matches": historical,
        "same_as_current": always_current,
        "verdict": verdict,
        "current": current,
    }


def renames_and_delistings(source: Recording, basic: pl.DataFrame, current: dict, day: str = "2010-06-01") -> dict:
    then = dict(source.securities_on(day).select("code", "name").iter_rows())
    delisted = basic.filter(
        (pl.col("status") == "0") & (pl.col("type") == "1") & (pl.col("outDate") > day) & (pl.col("ipoDate") < day)
    )
    sample = delisted.sort("outDate").head(5).rows(named=True)
    return {
        "day": day,
        "listed_then": len(then),
        "renamed": [(code, then.get(code), current.get(code)) for code in ("000001.SZ", "600018.SH", "000002.SZ")],
        "delisted": [
            (
                from_baostock(r["code"]),
                r["code_name"],
                r["outDate"],
                then.get(from_baostock(r["code"])),
                from_baostock(r["code"]) in current,
            )
            for r in sample
        ],
    }


# ---------------------------------------------------------------- 2.3 coverage


def coverage(
    source: Recording, store: Store, latest_names: dict, bj_new: str, bj_old: str
) -> tuple[dict, pl.DataFrame]:
    fields, rows = source.query("query_stock_basic")
    basic = pl.DataFrame(rows, schema={f: pl.String for f in fields}, orient="row")
    stocks = basic.filter(pl.col("type") == "1")
    bs_codes = {from_baostock(c) for c in stocks["code"]}
    seed_codes = set(store.read_table("securities", columns=["code"])["code"])
    out = {
        "fields": fields,
        "by_type_status": basic.group_by("type", "status").len().sort("type", "status").rows(),
        "bj_in_basic": basic.filter(pl.col("code").str.starts_with("bj.")).height,
        "bj_in_all_stock": sum(c.endswith(".BJ") for c in latest_names),
        "delisted_stocks": stocks.filter(pl.col("status") == "0").height,
        "only_baostock": sorted(bs_codes - seed_codes),
        "only_seed": sorted(seed_codes - bs_codes),
        "bj_daily": {},
    }
    for code in (bj_new, bj_old):
        try:
            bars = source.daily_bars(code, "2025-01-01", "2026-11-06")
            out["bj_daily"][code] = f"{bars.height} rows"
        except BaoStockError as error:
            out["bj_daily"][code] = f"error: {error}"
    return out, basic


# ---------------------------------------------------------------- 2.4 agreement with the seed


def reconcile_sample(store: Store, last_day: dt.date) -> list[dict]:
    sec = store.read_table("securities").filter(pl.col("code") != "601313.SH")
    rng = random.Random(20261108)
    picks = []
    stale = sec.filter((pl.col("last_date") < last_day - dt.timedelta(days=30)) & (pl.col("exchange") != "BJ")).sort(
        "code"
    )
    picks += rng.sample(stale.rows(named=True), 6)
    active = sec.filter(pl.col("last_date") >= last_day - dt.timedelta(days=30)).sort("code")
    for board, exchange, n in (
        ("main", "SH", 12),
        ("main", "SZ", 12),
        ("chinext", "SZ", 10),
        ("star", "SH", 10),
    ):  # BaoStock has no BJ
        pool = active.filter((pl.col("board") == board) & (pl.col("exchange") == exchange)).rows(named=True)
        picks += rng.sample(pool, n)
    return picks


def reconcile(source: Recording, store: Store, picks: list[dict]) -> dict:
    calendar = store.read_table("calendar")["date"]
    issues = store.read_table("data_issues", columns=["code", "date"])
    adj_seed = store.read_table("adj_factor")
    stats = {
        "stocks": [],
        "price_mismatch": [],
        "volume_over": [],
        "amount_over": [],
        "preclose": [],
        "suspended": [],
        "seed_gaps": [],
        "adj": [],
        "errors": [],
    }
    for pick in picks:
        code, end = pick["code"], pick["last_date"]
        start = end - dt.timedelta(days=730)
        seed = store.read_table("bars", codes=[code], start=start, end=end)
        try:
            bs = source.daily_bars(code, start, end)
            factors = source.adj_factors(code)
        except BaoStockError as error:
            stats["errors"].append((code, str(error)))
            continue
        delisted = pick["last_date"] < calendar.max() - dt.timedelta(days=30)
        stats["stocks"].append(
            (code, pick["board"], "delisted" if delisted else "listed", str(start), str(end), seed.height, bs.height)
        )
        if bs.height == 0:
            continue
        both = seed.join(bs, on=["code", "date"], suffix="_bs")
        traded = both.filter(pl.col("tradestatus_bs") == 1)
        for row in (
            traded.filter(
                (pl.col("open") != pl.col("open_bs"))
                | (pl.col("high") != pl.col("high_bs"))
                | (pl.col("low") != pl.col("low_bs"))
                | (pl.col("close") != pl.col("close_bs"))
            )
            .head(5)
            .rows(named=True)
        ):
            stats["price_mismatch"].append(
                (
                    code,
                    str(row["date"]),
                    row["open"],
                    row["open_bs"],
                    row["high"],
                    row["high_bs"],
                    row["low"],
                    row["low_bs"],
                    row["close"],
                    row["close_bs"],
                )
            )
        stats.setdefault("price_rows", 0)
        stats["price_rows"] += traded.height
        vol_err = (traded["volume"] - traded["volume_bs"]).abs() / traded["volume_bs"]
        amt_err = (traded["amount"] - traded["amount_bs"]).abs() / traded["amount_bs"]
        amt_bound = (500 / traded["amount_bs"]).clip(lower_bound=1e-6)
        stats.setdefault("volume_errors", []).extend(vol_err.to_list())
        stats.setdefault("amount_errors", []).extend(amt_err.to_list())
        for k in np.flatnonzero((vol_err > 1e-6).to_numpy())[:3]:
            stats["volume_over"].append(
                (code, str(traded["date"][int(k)]), traded["volume"][int(k)], traded["volume_bs"][int(k)])
            )
        for k in np.flatnonzero((amt_err > amt_bound).to_numpy())[:3]:
            stats["amount_over"].append(
                (code, str(traded["date"][int(k)]), traded["amount"][int(k)], traded["amount_bs"][int(k)])
            )

        ex_dates = set(adj_seed.filter(pl.col("code") == code)["ex_date"].to_list()) | set(factors["ex_date"].to_list())
        compared = traded.filter(pl.col("preclose").is_not_null())
        diff = compared.filter(pl.col("preclose") != pl.col("preclose_bs"))
        stats["preclose"].append(
            (code, compared.height, diff.height, sum(d in ex_dates for d in diff["date"].to_list()))
        )
        if diff.height:
            stats.setdefault("preclose_samples", []).extend(
                (code, str(r["date"]), r["preclose"], r["preclose_bs"], r["date"] in ex_dates)
                for r in diff.head(3).rows(named=True)
            )

        halted = bs.filter(pl.col("tradestatus") == 0)
        seed_days = set(seed["date"].to_list())
        for row in halted.head(3).rows(named=True):
            stats["suspended"].append(
                (
                    code,
                    str(row["date"]),
                    row["open"],
                    row["close"],
                    row["preclose"],
                    row["volume"],
                    row["amount"],
                    row["date"] in seed_days,
                )
            )
        stats.setdefault("halted_rows", 0)
        stats["halted_rows"] += halted.height
        stats.setdefault("halted_in_seed", 0)
        stats["halted_in_seed"] += sum(d in seed_days for d in halted["date"].to_list())
        span = (
            calendar.filter((calendar >= seed["date"].min()) & (calendar <= seed["date"].max())).to_list()
            if seed.height
            else []
        )
        flagged = set(issues.filter(pl.col("code") == code)["date"].to_list())
        gaps = [d for d in span if d not in seed_days and d not in flagged]
        bs_rows = dict(bs.select("date", "tradestatus").iter_rows())
        stats["seed_gaps"].append(
            (
                code,
                len(gaps),
                sum(bs_rows.get(d) == 0 for d in gaps),
                sum(d not in bs_rows for d in gaps),
                sum(bs_rows.get(d) == 1 for d in gaps),
            )
        )
        stats["adj"].append(compare_factors(code, adj_seed.filter(pl.col("code") == code), factors, seed))
    return stats


def compare_factors(code: str, seed: pl.DataFrame, bs: pl.DataFrame, bars: pl.DataFrame) -> dict:
    """Ex-date sets inside the window, and the step between consecutive factors (the bases differ, the steps should not)."""
    if bars.height == 0:
        return {"code": code}
    lo, hi = bars["date"].min(), bars["date"].max()
    seed = seed.sort("ex_date")
    bs = bs.sort("ex_date")
    seed_events = set(seed.filter((pl.col("ex_date") > lo) & (pl.col("ex_date") <= hi))["ex_date"].to_list())
    bs_events = set(bs.filter((pl.col("ex_date") > lo) & (pl.col("ex_date") <= hi))["ex_date"].to_list())

    def step(frame: pl.DataFrame, day: dt.date) -> float | None:
        before = frame.filter(pl.col("ex_date") < day)["factor"]
        at = frame.filter(pl.col("ex_date") == day)["factor"]
        return float(at[0] / before[-1]) if len(before) and len(at) else None

    errors = []
    for day in sorted(seed_events & bs_events):
        a, b = step(seed, day), step(bs, day)
        if a is not None and b is not None:
            errors.append(abs(a / b - 1))
    return {
        "code": code,
        "seed_only": sorted(map(str, seed_events - bs_events)),
        "bs_only": sorted(map(str, bs_events - seed_events)),
        "both": len(seed_events & bs_events),
        "step_errors": errors,
    }


def dropped_rows(source: Recording, store: Store, cases: dict[str, list[str]]) -> list[tuple]:
    """BaoStock's view of the rows behind the pending drop-rule decision."""
    issues = store.read_table("data_issues").filter(pl.col("table") == "bars")
    detail = {(r["code"], r["date"]): json.loads(r["detail"]) for r in issues.rows(named=True)}
    out = []
    for code, days in cases.items():
        dates = sorted(dt.date.fromisoformat(d) for d in days)
        try:
            bs = source.daily_bars(code, dates[0], dates[-1])
        except BaoStockError as error:
            out.append((code, "", "error", str(error)))
            continue
        seed = store.read_table("bars", codes=[code], start=dates[0], end=dates[-1])
        bs_by_day = {r["date"]: r for r in bs.rows(named=True)}
        seed_by_day = {r["date"]: r for r in seed.rows(named=True)}
        for day in dates:
            if (code, day) in detail:
                d = detail[(code, day)]
                seed_volume, seed_amount, seed_close = d["volume"], d["amount"], d["close"]
            elif day in seed_by_day:
                s = seed_by_day[day]
                seed_volume, seed_amount, seed_close = s["volume"], s["amount"], s["close"] / 1000
            else:
                continue
            b = bs_by_day.get(day)
            if b is None:
                out.append((code, str(day), "no BaoStock row", ""))
                continue
            per_share = round(b["amount"] / b["volume"], 4) if b["volume"] else None
            out.append(
                (
                    code,
                    str(day),
                    round(seed_close, 2),
                    _yuan(b["close"]),
                    seed_volume,
                    b["volume"],
                    round(seed_amount),
                    b["amount"],
                    per_share,
                    _yuan(b["low"]),
                    _yuan(b["high"]),
                )
            )
    return out


def _yuan(li: int | None) -> float | None:
    return None if li is None else li / 1000


# ---------------------------------------------------------------- 2.5 dividends and industry


def dividends(source: Recording, store: Store, codes: list[str]) -> dict:
    adj = store.read_table("adj_factor")
    out = {"stocks": [], "sample": None}
    for code in codes:
        actions = source.corporate_actions(code, 2021, 2026)
        seed = set(
            adj.filter((pl.col("code") == code) & (pl.col("ex_date") >= dt.date(2021, 1, 1)))["ex_date"].to_list()
        )
        bs = set(actions["ex_date"].drop_nulls().to_list())
        out["stocks"].append(
            (code, actions.height, len(bs & seed), sorted(map(str, bs - seed)), sorted(map(str, seed - bs)))
        )
        if out["sample"] is None and actions.height:
            out["sample"] = actions.head(3).rows()
    return out


def industry(source: Recording) -> dict:
    fields, rows = source.query("query_stock_industry")
    frame = pl.DataFrame(rows, schema={f: pl.String for f in fields}, orient="row")
    out = {
        "fields": fields,
        "rows": frame.height,
        "classifications": frame.group_by("industryClassification").len().rows()
        if "industryClassification" in fields
        else [],
        "update_dates": sorted(set(frame["updateDate"])) if "updateDate" in fields else [],
        "industries": frame["industry"].n_unique() if "industry" in fields else None,
    }
    try:
        f2, r2 = source.query("query_stock_industry", code="sh.600000", date="2015-01-05")
        out["dated_query"] = (f2, r2[:3])
    except BaoStockError as error:
        out["dated_query"] = str(error)
    return out


# ---------------------------------------------------------------- 2.6 backfill estimates


def estimates(store: Store, measured: dict) -> dict:
    """Requests and hours per backfill plan, for one session: BaoStock keeps one session per user, so
    parallel processes do not add throughput. Time = rows / measured rows per second.

    measured: full_rows_per_s (one stock's full history), snapshot_rows_per_s (one day, every stock),
    small_latency (one-year query, seconds), names_historical (bool).
    """
    first_year = {2000: dt.date(2000, 1, 1), 2005: dt.date(2005, 1, 1)}
    bars = store.scan("bars", columns=["code", "date"]).filter(~pl.col("code").str.ends_with(".BJ")).collect()
    out = {"stocks": bars["code"].n_unique()}
    for year, start in first_year.items():
        rows = bars.filter(pl.col("date") >= start)
        per_code = rows.group_by("code").len()["len"]
        per_day = rows.group_by("date").len()["len"]
        out[f"A_{year}"] = {
            "requests": int((per_code // 2000 + 1).sum()),  # every full page triggers one more fetch
            "hours": rows.height / measured["full_rows_per_s"] / 3600,
        }
        out[f"D_{year}"] = {"requests": per_day.len(), "hours": rows.height / measured["snapshot_rows_per_s"] / 3600}
        out[f"B_{year}"] = {"requests": int((per_day // 2000 + 1).sum()), "usable": measured["names_historical"]}
    code_years = bars.select(pl.col("code"), pl.col("date").dt.year()).unique().height
    out["dividends"] = {"requests": code_years, "hours": code_years * measured["small_latency"] / 3600}
    out["C_per_stock"] = {"requests": out["stocks"], "minutes": out["stocks"] * measured["small_latency"] / 60}
    return out


def measured(r: dict) -> dict:
    full = r["speed"]["full_history"]
    snaps = [e["rows"] / e["seconds"] for e in r["snapshots"]["query_daily_history_k_AStock"] if "seconds" in e]
    return {
        "full_rows_per_s": full["rows"] / full["seconds"],
        "snapshot_rows_per_s": float(np.median(snaps)),
        "small_latency": r["speed"]["single"]["median"],
        "names_historical": r["names"]["verdict"].startswith("historical"),
    }


# ---------------------------------------------------------------- report


def render(r: dict) -> str:
    out = [f"# BaoStock probe {r['date']}", ""]
    out += [
        f"Requests used: **{r['requests']:,}** of a {BUDGET:,} budget (logins, logouts and extra pages included; parallel workers: {r['parallel_requests']:,}). Elapsed: {r['elapsed'] / 60:.1f} min.",
        "",
    ]
    for name, error in r["section_errors"].items():
        out += [f"- **{name} stopped:** {error}"]
    if r["section_errors"]:
        out.append("")
    out += (
        render_speed(r)
        + render_names(r)
        + render_coverage(r)
        + render_reconcile(r)
        + render_dropped(r)
        + render_dividends(r)
        + render_estimates(r)
    )
    out += ["## Fields each method returned", ""]
    for method, sample in r["samples"].items():
        out += [f"- `{method}`: {', '.join(sample['fields'])}"]
    return "\n".join(out) + "\n"


def table(header: list[str], rows) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(" --- " for _ in header) + "|",
        *("| " + " | ".join(str(x) for x in row) + " |" for row in rows),
        "",
    ]


def render_speed(r: dict) -> list[str]:
    s = r.get("speed")
    if not s:
        return ["## 2.1 Speed and limits", "", "Not run.", ""]
    one, full = s["single"], s["full_history"]
    out = ["## 2.1 Speed and limits", ""]
    out += [
        f"- **Single process, {one['n']} one-year daily queries:** median {one['median']:.3f} s, p95 {one['p95']:.3f} s, max {one['max']:.3f} s; {one['calls_per_s']:.2f} calls/s, {one['rows_per_s']:.0f} rows/s. Errors: {one['errors'] or 'none'}.",
        f"- **Full history of {full['code']}:** {full['rows']:,} rows from {full['first']} in {full['seconds']:.2f} s and {full['requests']} requests (2,000 rows per page).",
        "",
    ]
    rows = [
        (
            n,
            p.get("calls"),
            f"{p.get('median', float('nan')):.3f}",
            f"{p.get('p95', float('nan')):.3f}",
            f"{p['calls_per_s']:.2f}",
            f"{p['rows_per_s']:.0f}",
            "; ".join(p["errors"]) or "none",
        )
        for n, p in s["parallel"].items()
    ]
    out += table(["processes", "calls", "median s", "p95 s", "calls/s", "rows/s", "errors"], rows)
    out += ["Each worker logs in on its own; a worker kicked out by another login would report error 10001001.", ""]
    snap = r.get("snapshots", {})
    if snap:
        out += ["Per-day cross-section queries (baostock 0.9):", ""]
        rows = []
        for method, entries in snap.items():
            for e in entries:
                rows.append(
                    (
                        method,
                        e["day"],
                        e.get("rows", ""),
                        f"{e['seconds']:.2f}" if "seconds" in e else "",
                        e.get("suspended", ""),
                        e.get("st", ""),
                        ",".join(e.get("exchanges", [])),
                        e.get("error", ""),
                    )
                )
        out += table(["method", "date", "rows", "seconds", "tradestatus 0", "isST 1", "exchanges", "error"], rows)
    return out


def render_names(r: dict) -> list[str]:
    n = r.get("names")
    if not n:
        return ["## 2.2 Does query_all_stock(day) return historical names?", "", "Not run.", ""]
    out = ["## 2.2 Does query_all_stock(day) return historical names?", "", f"**Verdict: {n['verdict']}.**", ""]
    out += [
        f"Scanned {n['scanned']} stocks named ST on {n['latest']} for isST changes since 2016; {len(n['changes'])} changed. Around each one's latest change, {n['matches']} of {len(n['checks'])} sampled days have \"ST\" in the name exactly when isST = 1; {n['same_as_current']} carry today's name.",
        "",
    ]
    out += table(["code", "date", "isST", "name that day", "name today"], n["checks"])
    rd = r.get("renames")
    if rd:
        out += [f"On {rd['day']} ({rd['listed_then']:,} codes listed):", ""]
        out += table(["code", f"name on {rd['day']}", "name today"], rd["renamed"])
        out += table(
            ["delisted code", "name (stock_basic)", "delisted", f"name on {rd['day']}", "in today's list"],
            rd["delisted"],
        )
    return out


def render_coverage(r: dict) -> list[str]:
    c = r.get("coverage")
    if not c:
        return ["## 2.3 Coverage", "", "Not run.", ""]
    out = ["## 2.3 Coverage", ""]
    out += [
        f"- **query_stock_basic fields:** {', '.join(c['fields'])}.",
        f"- **Beijing exchange:** {c['bj_in_basic']} bj codes in query_stock_basic, {c['bj_in_all_stock']} in query_all_stock. Daily bars: "
        + "; ".join(f"{k}: {v}" for k, v in c["bj_daily"].items())
        + ".",
        f"- **Delisted stocks (type 1, status 0):** {c['delisted_stocks']:,}.",
        f"- **Stock codes only in BaoStock:** {len(c['only_baostock']):,}, e.g. {', '.join(c['only_baostock'][:8])}.",
        f"- **Stock codes only in the seed:** {len(c['only_seed']):,}, e.g. {', '.join(c['only_seed'][:8])}.",
        "",
    ]
    out += table(["type", "status", "codes"], c["by_type_status"])
    return out


def render_reconcile(r: dict) -> list[str]:
    s = r.get("reconcile")
    if not s:
        return ["## 2.4 Agreement with the seed", "", "Not run.", ""]
    vol, amt = np.array(s.get("volume_errors", [])), np.array(s.get("amount_errors", []))
    pc = s["preclose"]
    pc_rows, pc_diff, pc_ex = sum(p[1] for p in pc), sum(p[2] for p in pc), sum(p[3] for p in pc)
    gaps = s["seed_gaps"]
    step = np.array([e for a in s["adj"] for e in a.get("step_errors", [])])
    out = ["## 2.4 Agreement with the seed", ""]
    out += [
        f"- **Stocks:** {len(s['stocks'])} compared over the last two years of seed data; errors: {s['errors'] or 'none'}.",
        f"- **Prices (traded days, rounded to 0.01 元):** {len(s['price_mismatch'])} mismatching rows shown of {s.get('price_rows', 0):,} compared.",
        f"- **Volume:** {int((vol > 1e-6).sum())} of {vol.size:,} rows above 1e-6 relative error (max {vol.max() if vol.size else float('nan'):.2e}).",
        f"- **Amount:** {len(s['amount_over'])} rows shown above max(1e-6, 500 元 / amount); max relative error {amt.max() if amt.size else float('nan'):.2e}.",
        f"- **Preclose:** {pc_diff:,} of {pc_rows:,} rows differ from the seed's derived preclose; {pc_ex:,} of them fall on an ex-date.",
        f"- **Suspended days:** BaoStock returned {s.get('halted_rows', 0):,} rows with tradestatus 0; {s.get('halted_in_seed', 0)} of them have a seed row.",
        f"- **Seed gaps** (trading days without a seed row and without a data_issues entry): {sum(g[1] for g in gaps):,}. BaoStock has tradestatus 0 on {sum(g[2] for g in gaps):,}, no row on {sum(g[3] for g in gaps):,}, a traded row on {sum(g[4] for g in gaps):,}.",
        f"- **Adjustment steps:** {sum(a.get('both', 0) for a in s['adj'])} ex-dates on both sides, {sum(len(a.get('seed_only', [])) for a in s['adj'])} only in the seed, {sum(len(a.get('bs_only', [])) for a in s['adj'])} only in BaoStock. Step relative error: median {np.median(step) if step.size else float('nan'):.2e}, p95 {np.quantile(step, 0.95) if step.size else float('nan'):.2e}, max {step.max() if step.size else float('nan'):.2e}.",
        "",
    ]
    out += table(["code", "board", "status", "from", "to", "seed rows", "BaoStock rows"], s["stocks"])
    if s["price_mismatch"]:
        out += ["Price mismatches (厘, seed vs BaoStock):", ""] + table(
            ["code", "date", "open", "open bs", "high", "high bs", "low", "low bs", "close", "close bs"],
            s["price_mismatch"],
        )
    if s["volume_over"]:
        out += ["Volume above the bound:", ""] + table(["code", "date", "seed", "BaoStock"], s["volume_over"])
    if s["amount_over"]:
        out += ["Amount above the bound:", ""] + table(["code", "date", "seed", "BaoStock"], s["amount_over"])
    if s.get("preclose_samples"):
        out += ["Preclose differences (厘):", ""] + table(
            ["code", "date", "seed (derived)", "BaoStock", "ex-date"], s["preclose_samples"][:15]
        )
    if s["suspended"]:
        out += ["BaoStock rows on suspended days (厘 / shares / yuan):", ""] + table(
            ["code", "date", "open", "close", "preclose", "volume", "amount", "seed has a row"], s["suspended"][:15]
        )
    gap_rows = [g for g in gaps if g[1]]
    if gap_rows:
        out += ["Seed gaps by stock:", ""] + table(
            ["code", "gaps", "BaoStock tradestatus 0", "no BaoStock row", "BaoStock traded"], gap_rows
        )
    diffs = [
        (a["code"], ", ".join(a["seed_only"]) or "-", ", ".join(a["bs_only"]) or "-")
        for a in s["adj"]
        if a.get("seed_only") or a.get("bs_only")
    ]
    if diffs:
        out += ["Ex-dates on one side only:", ""] + table(["code", "seed only", "BaoStock only"], diffs)
    return out


def render_dropped(r: dict) -> list[str]:
    rows = r.get("dropped")
    if not rows:
        return []
    out = ["## 2.4b BaoStock on the rows behind the drop-rule decision", ""]
    out += table(
        [
            "code",
            "date",
            "seed close",
            "bs close",
            "seed volume",
            "bs volume",
            "seed amount",
            "bs amount",
            "bs average price",
            "bs low",
            "bs high",
        ],
        rows,
    )
    return out


def render_dividends(r: dict) -> list[str]:
    d, ind = r.get("dividends"), r.get("industry")
    out = ["## 2.5 Dividends and industry", ""]
    if d:
        out += table(
            ["code", "actions 2021-2026", "ex-dates matching the seed", "BaoStock only", "seed only"], d["stocks"]
        )
        if d["sample"]:
            out += ["Sample corporate actions:", ""] + table(
                [
                    "code",
                    "ex_date",
                    "record_date",
                    "pay_date",
                    "cash before tax",
                    "bonus shares",
                    "transfer shares",
                    "plan",
                ],
                d["sample"],
            )
    if ind:
        out += [
            f"- **query_stock_industry fields:** {', '.join(ind['fields'])}; {ind['rows']:,} rows, {ind['industries']} industries.",
            f"- **Classification:** {ind['classifications']}.",
            f"- **updateDate values:** {', '.join(ind['update_dates'][:10])}{' …' if len(ind['update_dates']) > 10 else ''}.",
            f"- **A dated query (sh.600000, 2015-01-05) returned:** {ind['dated_query']}.",
            "",
        ]
    return out


def render_estimates(r: dict) -> list[str]:
    e = r.get("estimates")
    if not e:
        return ["## 2.6 Backfill estimates", "", "Not run.", ""]
    names = "" if e["B_2000"]["usable"] else "; names are today's, so no ST history"
    out = ["## 2.6 Backfill estimates", ""]
    out += [
        f"One session only (a second login ends the first), {e['stocks']:,} SH/SZ stocks; hours = rows (traded days) / measured rows per second.",
        "",
    ]
    out += table(
        ["plan", "from 2000", "from 2005", "note"],
        [
            (
                "A: each stock's full history",
                f"{e['A_2000']['requests']:,} req, {e['A_2000']['hours']:.1f} h",
                f"{e['A_2005']['requests']:,} req, {e['A_2005']['hours']:.1f} h",
                "pages of 2,000 rows",
            ),
            (
                "D: query_daily_history_k_AStock per day",
                f"{e['D_2000']['requests']:,} req, {e['D_2000']['hours']:.1f} h",
                f"{e['D_2005']['requests']:,} req, {e['D_2005']['hours']:.1f} h",
                "every SH/SZ stock incl. suspended, with tradestatus, isST, preclose",
            ),
            (
                "B: query_all_stock per day",
                f"{e['B_2000']['requests']:,} req",
                f"{e['B_2005']['requests']:,} req",
                "tradestatus only" + names,
            ),
            (
                "Dividend details, one query per stock-year",
                f"{e['dividends']['requests']:,} req, {e['dividends']['hours']:.1f} h",
                "",
                "query_dividend_data has no per-day form",
            ),
            (
                "C: daily update, one query per stock",
                f"{e['C_per_stock']['requests']:,} req, {e['C_per_stock']['minutes']:.0f} min",
                "",
                "",
            ),
            (
                "C: daily update by cross-section",
                "3 to 5 req, < 1 min",
                "",
                "AStock + daily_adjust_factor + stock_basic for listings",
            ),
        ],
    )
    return out


# ---------------------------------------------------------------- main


def main() -> None:
    store = Store()
    manifest = store.manifest()
    last_day = dt.date.fromisoformat(manifest["last_trade_date"])
    started = time.perf_counter()
    source = Recording(BUDGET)
    r: dict = {"date": dt.date.today().isoformat(), "section_errors": {}}
    securities = store.read_table("securities")

    def section(name: str, call):
        try:
            return call()
        except Exception as error:  # one section failing must not lose the others
            r["section_errors"][name] = f"{type(error).__name__}: {error}"
            return None

    source.login()
    try:
        long_listed = sorted(
            securities.filter(
                (pl.col("first_date") < dt.date(2015, 1, 1))
                & (pl.col("last_date") >= last_day)
                & pl.col("exchange").is_in(["SH", "SZ"])
            )["code"].to_list()
        )
        random.Random(7).shuffle(long_listed)
        tasks = [(code, year) for code in long_listed[:80] for year in range(2016, 2026)]
        random.Random(8).shuffle(tasks)
        r["speed"] = section("2.1 speed", lambda: speed(source, tasks, "600000.SH"))
        r["parallel_requests"] = sum(p["requests"] for p in (r["speed"] or {}).get("parallel", {}).values())
        source.budget = BUDGET - r["parallel_requests"]
        source.login()  # the workers' logins end this process's session: BaoStock keeps one session per user
        r["snapshots"] = section(
            "2.1 snapshots",
            lambda: snapshots(source, [str(last_day), "2020-06-01", "2010-06-01", "2005-06-01", "2000-06-01"]),
        )
        r["names"] = section("2.2 names", lambda: st_names(source, str(last_day)))
        cov = section(
            "2.3 coverage",
            lambda: coverage(source, store, (r["names"] or {}).get("current", {}), "920047.BJ", "830799.BJ"),
        )
        if cov:
            r["coverage"], basic = cov
            r["renames"] = section(
                "2.2 renames",
                lambda: renames_and_delistings(source, basic, r["names"]["current"]) if r["names"] else None,
            )
        picks = reconcile_sample(store, last_day)
        r["reconcile"] = section("2.4 reconcile", lambda: reconcile(source, store, picks))
        cases = {
            "601313.SH": ["2012-01-16", "2015-07-08", "2017-11-07", "2018-02-14"],
            "600682.SH": ["2000-02-14"],
            "600770.SH": ["2000-02-14"],
            "000003.SZ": ["2001-04-24"],
            "000014.SZ": ["2001-04-24"],
            "000029.SZ": ["2000-03-01"],
            "000521.SZ": ["2000-03-01"],
            "000503.SZ": ["2000-02-16"],
            "000682.SZ": ["2000-03-01"],
            "831305.BJ": ["2023-12-26"],
            "834599.BJ": ["2023-12-27", "2024-01-08"],
            "920553.BJ": ["2026-11-05"],
            "301487.SZ": ["2023-08-09"],
            "601091.SH": ["2026-09-18"],
        }
        r["dropped"] = section("2.4b dropped rows", lambda: dropped_rows(source, store, cases))
        r["dividends"] = section(
            "2.5 dividends",
            lambda: dividends(source, store, ["600519.SH", "000001.SZ", "600036.SH", "000651.SZ", "601318.SH"]),
        )
        r["industry"] = section("2.5 industry", lambda: industry(source))
    finally:
        source.logout()
    if r["speed"] and r["snapshots"] and r["names"]:
        r["estimates"] = estimates(store, measured(r))
    if r.get("names"):
        r["names"].pop("current", None)
    r["samples"] = source.samples
    r["requests"] = source.requests + r.get("parallel_requests", 0)
    r["elapsed"] = time.perf_counter() - started
    path = store.home / "reports" / f"baostock_probe_{r['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(r), "utf-8")
    path.with_suffix(".results.json").write_text(json.dumps(r, ensure_ascii=False, default=str), "utf-8")
    path.with_suffix(".samples.json").write_text(
        json.dumps(source.samples, ensure_ascii=False, indent=1, default=str), "utf-8"
    )
    print(f"requests {r['requests']}; report {path}")


if __name__ == "__main__":
    main()
