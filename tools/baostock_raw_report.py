"""Report on the raw BaoStock layer against the seed store, from local files only (sends no request).

Writes $WEQUANT_HOME/reports/baostock_raw_<date>.md. Run: python -m tools.baostock_backfill report
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import polars as pl

from tools.baostock_backfill import Progress, shanghai_today, stock_codes, trading_days
from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store, wequant_home

SAMPLE_DAYS_PER_YEAR = 3


def write_report(home: Path | None = None) -> Path:
    home = Path(home or wequant_home())
    raw, store = BaoStockRaw.at(home), Store(home)
    sections = [
        f"# BaoStock raw layer {dt.date.today().isoformat()}",
        "",
        *progress_section(raw),
        *calendar_section(raw, store),
        *yearly_section(raw),
        *missing_codes_section(raw, store),
        *sample_section(raw, store),
        *factor_section(raw, store),
    ]
    path = home / "reports" / f"baostock_raw_{dt.date.today().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections) + "\n", "utf-8")
    return path


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def daily_frame(raw: BaoStockRaw, columns: list[str]) -> pl.DataFrame:
    paths = sorted(str(p) for p in (raw.root / "daily").glob("*/*.parquet"))
    return (
        pl.scan_parquet(paths).select(columns).collect()
        if paths
        else pl.DataFrame(schema={c: pl.String for c in columns})
    )


def li(column: str) -> pl.Expr:
    """Raw price text rounded to 0.01 元, in 厘."""
    return ((pl.col(column).cast(pl.Float64) * 100).round(0) * 10).cast(pl.Int32)


def progress_section(raw: BaoStockRaw) -> list[str]:
    progress = Progress(raw.root / "progress.json")
    days = trading_days(raw, shanghai_today()) if raw.trade_dates.exists() else []
    stocks = stock_codes(raw) if raw.stock_basic.exists() else []
    done_days, done_stocks = raw.daily_days(), raw.adjust_factor_codes()
    failures = list(progress.failures.values())
    seconds = sum(
        (dt.datetime.fromisoformat(r["ended"]) - dt.datetime.fromisoformat(r["started"])).total_seconds()
        for r in progress.runs
        if "ended" in r
    )
    size = sum(p.stat().st_size for p in raw.root.rglob("*.parquet"))
    meta = sum(p.exists() for p in (raw.stock_basic, raw.trade_dates, raw.stock_industry))
    return [
        "## 1. Progress",
        "",
        *table(
            ["phase", "done", "of", "failures"],
            [
                ("0 reference data", meta, 3, sum(f["phase"] == 0 for f in failures)),
                (
                    "1 one-day snapshots",
                    f"{len(done_days & set(days)):,}",
                    f"{len(days):,}",
                    sum(f["phase"] == 1 for f in failures),
                ),
                (
                    "2 adjustment factors",
                    f"{len(done_stocks & set(stocks)):,}",
                    f"{len(stocks):,}",
                    sum(f["phase"] == 2 for f in failures),
                ),
            ],
        ),
        f"- **Requests:** {progress.requests:,} over {len(progress.runs)} runs; time spent running: {seconds / 3600:.1f} h.",
        f"- **Disk:** {size / 2**20:.0f} MB in raw/baostock.",
        *(
            [f"- **Failure list:** {', '.join(f'{f["item"]} ({f["error"]})' for f in failures[:20])}"]
            if failures
            else []
        ),
        "",
    ]


def calendar_section(raw: BaoStockRaw, store: Store) -> list[str]:
    seed = set(store.read_table("calendar")["date"].to_list())
    if not raw.trade_dates.exists() or not seed:
        return ["## 2. Calendars", "", "Not available.", ""]
    lo, hi = min(seed), max(seed)
    dates = pl.read_parquet(raw.trade_dates).filter(pl.col("is_trading_day") == "1")["calendar_date"]
    baostock = {d for d in (dt.date.fromisoformat(x) for x in dates) if lo <= d <= hi}
    only_bs, only_seed = sorted(baostock - seed), sorted(seed - baostock)
    return [
        "## 2. Calendars",
        "",
        f"Between {lo} and {hi}: BaoStock {len(baostock):,} trading days, seed {len(seed):,}.",
        "",
        f"- **Only in BaoStock ({len(only_bs)}):** {', '.join(map(str, only_bs)) or 'none'}",
        f"- **Only in the seed ({len(only_seed)}):** {', '.join(map(str, only_seed)) or 'none'}",
        "",
    ]


def yearly_section(raw: BaoStockRaw) -> list[str]:
    daily = daily_frame(raw, ["date", "code", "tradestatus", "isST"])
    if daily.height == 0:
        return ["## 3. Rows by year", "", "No snapshots yet.", ""]
    years = (
        daily.group_by(pl.col("date").str.slice(0, 4).alias("year"))
        .agg(
            rows=pl.len(),
            codes=pl.col("code").n_unique(),
            suspended=(pl.col("tradestatus") == "0").sum(),
            st=(pl.col("isST") == "1").sum(),
        )
        .sort("year")
    )
    rows = [(y, f"{n:,}", f"{c:,}", f"{s:,}", f"{st:,}") for y, n, c, s, st in years.rows()]
    return ["## 3. Rows by year", "", *table(["year", "rows", "codes", "tradestatus 0", "isST 1"], rows)]


def missing_codes_section(raw: BaoStockRaw, store: Store) -> list[str]:
    seed = set(store.read_table("securities", columns=["code"])["code"])
    daily = daily_frame(raw, ["code"])
    counts = daily.group_by("code").len()
    in_daily = {f"{c[3:]}.{c[:2].upper()}": n for c, n in counts.rows()}
    missing = sorted(c for c in in_daily if c not in seed)
    basic = pl.read_parquet(raw.stock_basic) if raw.stock_basic.exists() else None
    delisted = set()
    if basic is not None:
        out = basic.filter((pl.col("type") == "1") & (pl.col("status") == "0"))["code"]
        delisted = {f"{c[3:]}.{c[:2].upper()}" for c in out}
    rows = [(c, "delisted" if c in delisted else "listed", f"{in_daily[c]:,}") for c in missing]
    return [
        "## 4. SH/SZ codes BaoStock has and the seed lacks",
        "",
        f"{len(missing)} codes appear in the snapshots but not in the seed's securities; {sum(c in delisted for c in missing)} of them are delisted "
        f"(BaoStock lists {len(delisted)} delisted stocks, {len(delisted - seed)} of them missing from the seed). "
        f"Together they have {sum(in_daily[c] for c in missing):,} daily rows the seed does not have.",
        "",
        *(table(["code", "status", "BaoStock rows"], rows) if rows else []),
    ]


def sample_section(raw: BaoStockRaw, store: Store) -> list[str]:
    days = sorted(raw.daily_days())
    rng = random.Random(20261109)
    by_year: dict[int, list[dt.date]] = {}
    for day in days:
        by_year.setdefault(day.year, []).append(day)
    picks = sorted(
        d for year_days in by_year.values() for d in rng.sample(year_days, min(SAMPLE_DAYS_PER_YEAR, len(year_days)))
    )
    if not picks:
        return ["## 5. Sample comparison with the seed", "", "No snapshots yet.", ""]
    adj = store.read_table("adj_factor", columns=["code", "ex_date"])
    ex_dates = set(adj.rows())
    rows, total = [], {"rows": 0, "match": 0, "pre": 0, "pre_diff": 0, "pre_ex": 0}
    for day in picks:
        bs = (
            pl.read_parquet(raw.daily(day))
            .filter(pl.col("tradestatus") == "1")
            .select(
                code=pl.col("code").str.slice(3) + "." + pl.col("code").str.slice(0, 2).str.to_uppercase(),
                open_bs=li("open"),
                high_bs=li("high"),
                low_bs=li("low"),
                close_bs=li("close"),
                preclose_bs=li("preclose"),
            )
        )
        seed = store.read_table(
            "bars", start=day, end=day, columns=["code", "open", "high", "low", "close", "preclose"]
        )
        both = seed.join(bs, on="code")
        same = (
            (pl.col("open") == pl.col("open_bs"))
            & (pl.col("high") == pl.col("high_bs"))
            & (pl.col("low") == pl.col("low_bs"))
            & (pl.col("close") == pl.col("close_bs"))
        )
        match = both.filter(same).height
        pre = both.filter(pl.col("preclose").is_not_null())
        diff = pre.filter(pl.col("preclose") != pl.col("preclose_bs"))
        on_ex = sum((code, day) in ex_dates for code in diff["code"])
        rows.append((day, f"{both.height:,}", f"{match / both.height:.4%}" if both.height else "-", diff.height, on_ex))
        for key, value in (
            ("rows", both.height),
            ("match", match),
            ("pre", pre.height),
            ("pre_diff", diff.height),
            ("pre_ex", on_ex),
        ):
            total[key] += value
    share = total["match"] / total["rows"] if total["rows"] else float("nan")
    return [
        "## 5. Sample comparison with the seed",
        "",
        f"{SAMPLE_DAYS_PER_YEAR} random trading days per year; traded rows present on both sides; prices rounded to 0.01 元, in 厘.",
        "",
        f"- **Open/high/low/close identical:** {total['match']:,} of {total['rows']:,} rows ({share:.4%}).",
        f"- **Preclose:** the exchange's preclose differs from the seed's derived one on {total['pre_diff']:,} of {total['pre']:,} rows; "
        f"{total['pre_ex']:,} of those fall on a seed ex-date.",
        "",
        *table(["date", "rows", "OHLC identical", "preclose differs", "on a seed ex-date"], rows),
    ]


def factor_section(raw: BaoStockRaw, store: Store) -> list[str]:
    paths = sorted(str(p) for p in (raw.root / "adjust_factor").glob("*.parquet"))
    if not paths:
        return ["## 6. Adjustment factors", "", "No adjustment factor files yet.", ""]
    bs = pl.scan_parquet(paths).select("code", "dividOperateDate").collect()
    bs_years = bs.group_by(pl.col("dividOperateDate").str.slice(0, 4).alias("year")).len()
    codes = {f"{c[3:]}.{c[:2].upper()}" for c in bs["code"].unique()}
    seed = store.read_table("adj_factor").join(
        store.read_table("securities", columns=["code", "first_date"]), on="code"
    )
    events = seed.filter((pl.col("ex_date") > pl.col("first_date")) & pl.col("code").is_in(list(codes)))
    seed_years = events.group_by(pl.col("ex_date").dt.year().cast(pl.String).alias("year")).len()
    joined = bs_years.join(seed_years, on="year", how="full", coalesce=True, suffix="_seed").sort("year").fill_null(0)
    rows = [(y, f"{b:,}", f"{s:,}", f"{b - s:+,}") for y, b, s in joined.rows()]
    return [
        "## 6. Adjustment factors",
        "",
        f"Ex-dates per year for the {len(codes):,} stocks with a factor file. The seed counts exclude each stock's first row.",
        "",
        *table(["year", "BaoStock", "seed", "difference"], rows),
    ]


if __name__ == "__main__":
    print(write_report())
