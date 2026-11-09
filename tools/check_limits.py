"""Price-limit check: every bar against its exchange limits; breaches void that day's limits through data_issues.

Run: python -m tools.check_limits [--home H] [--commit] [--out FILE]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from wequant.data.files import atomic_write
from wequant.data.store import Store
from wequant.rules.book import NewListing, RuleBook, default_book
from wequant.rules.check import ACTION, KIND, missing_issues, void
from wequant.rules.limits import FREE, LIMITED, LIMITS_FORMAT, UNKNOWN, down_price, up_price
from wequant.rules.rounding import round_half_up
from wequant.rules.tables import CheckResult, check_store, limit_check_current
from wequant.version import code_version

STATES = (("LIMITED", LIMITED), ("FREE", FREE), ("UNKNOWN", UNKNOWN))
HINTS = ("resumed", "ex_date", "preclose_derived", "other")
CLUSTER_CODES = 5
SAMPLE = 20
STATE_COLUMNS = ["code", "date", "limit_up", "limit_down", "limit_rule", "limit_state"]
UNMODELLED_LIMITS = (
    "The 5% band of unreformed S-shares from 2007-01-08 (exchange notice of 2007-01-05; about 194 stocks, 91 of them "
    "already ST), PT stocks and share-reform resumption days.",
    "The first day after 恢复上市 (a suspended listing resumes) or 重新上市 (relisting), which has no limit.",
    "The delisting period (退市整理期): a 10% main-board band even for ST stocks, and since 2020 no limit on its first "
    "day.",
)
UNMODELLED_OTHER = (
    "The transfer-fee minimum of 1 元 and par values other than 1 元 (e.g. 601899.SH 0.1, 603993.SH 0.2).",
    "The tick guards (a limit at least one tick from the preclose and at least one tick): the exchange text was not read.",
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store, book = Store(args.home), default_book()
    result = check_store(store, commit=args.commit, book=book)
    version = result.committed or result.version
    if args.commit:
        verify(store, result, version, book)
    text = "\n".join(report(store, result, book, version, args.commit)) + "\n"
    if args.commit:
        path = report_path(store, version)
        atomic_write(path, text.encode("utf-8"))
        print(path)
    if args.out:
        Path(args.out).write_text(text, "utf-8")
    elif not args.commit:
        sys.stdout.write(text)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.check_limits", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, default=None, help="store home (default: WEQUANT_HOME)")
    parser.add_argument("--commit", action="store_true", help="record breaches and the limit_check block")
    parser.add_argument("--out", type=Path, default=None, help="write the report here instead of stdout")
    return parser.parse_args(argv)


def verify(store: Store, result: CheckResult, version: int, book: RuleBook) -> None:
    """Fail unless the committed version records exactly the breaches found and a current limit_check block."""
    manifest = store.manifest(version)
    recorded = store.read_table("data_issues", manifest=manifest).filter(pl.col("kind") == KIND)
    differ = missing_issues(result.issues, recorded).height or missing_issues(recorded, result.issues).height
    if differ or not limit_check_current(manifest, book):
        raise RuntimeError(f"version {version} does not hold the limit check just committed")


def report_path(store: Store, version: int) -> Path:
    last = store.read_table("calendar", columns=["date"], version=version)["date"].max()
    return store.home / "reports" / f"check_limits_{last.isoformat()}_v{version}.md"


def report(store: Store, result: CheckResult, book: RuleBook, version: int, commit: bool) -> list[str]:
    issues = store.read_table("data_issues", version=version)
    frame = result.frame
    return [
        *header(store, result, book, version, commit),
        *states_section(frame, issues),
        *listing_section(frame, issues),
        *breaches_section(frame, result),
        *recorded_section(result, version, commit),
        *rules_section(book),
        *evidence_section(frame, book),
    ]


def header(store: Store, result: CheckResult, book: RuleBook, version: int, commit: bool) -> list[str]:
    manifest = store.manifest(version)
    current = "yes" if limit_check_current(manifest, book) else "no"
    if "limit_check" not in manifest:
        current += " (none recorded)"
    if not commit:
        mode = "dry run; nothing written"
    elif result.committed:
        mode = f"committed version {result.committed}"
    else:
        mode = "committed nothing: the recorded check was already current"
    return [
        f"# Price-limit check, store v{result.version}",
        "",
        f"- **Store version checked:** {result.version} (last trade date {manifest.get('last_trade_date', '?')})",
        f"- **Mode:** {mode}",
        f"- **Rules:** `{book.digest}`, limit format {LIMITS_FORMAT}",
        f"- **Code version:** {code_version()}",
        f"- **Recorded check current at v{version}:** {current}",
        "",
    ]


def table(columns: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(" --- " for _ in columns) + "|"]
    return lines + ["| " + " | ".join(_cell(x) for x in row) + " |" for row in rows] + [""]


def _cell(value) -> str:
    if value is None:
        return ""
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def hint() -> pl.Expr:
    """The likeliest cause of a breach, in order: a resumption after a gap, an ex-date, a derived preclose."""
    return (
        pl.when(pl.col("gap_days") > 0)
        .then(pl.lit("resumed"))
        .when(pl.col("ex_date"))
        .then(pl.lit("ex_date"))
        .when(pl.col("preclose_derived"))
        .then(pl.lit("preclose_derived"))
        .otherwise(pl.lit("other"))
        .alias("hint")
    )


def states_section(frame: pl.DataFrame, issues: pl.DataFrame) -> list[str]:
    recorded = issues.filter(pl.col("kind") == KIND)
    states = void(frame.select(STATE_COLUMNS).with_columns(checked=pl.col("limit_state")), recorded)
    counts = dict(states.group_by("limit_state").len().iter_rows())
    unknown = states.filter(pl.col("limit_state") == UNKNOWN)
    free = states.filter(pl.col("limit_state") == FREE).group_by("limit_rule").len().sort("limit_rule")
    has_rule = pl.col("limit_rule").is_not_null()
    return [
        "## 1. Limit states",
        "",
        f"{frame.height:,} bars, counted as load_limits returns them: the {ACTION} rows recorded at this version "
        "make their bars UNKNOWN.",
        "",
        *table(["state", "bars"], [(name, counts.get(code, 0)) for name, code in STATES]),
        "FREE bars by rule:",
        "",
        *table(["rule", "bars"], free.iter_rows()),
        "UNKNOWN bars by cause (no rule must be 0):",
        "",
        *table(
            ["cause", "bars"],
            [
                ("no rule", unknown.filter(~has_rule).height),
                ("no preclose", unknown.filter(has_rule & (pl.col("checked") == UNKNOWN)).height),
                ("voided", unknown.filter(has_rule & (pl.col("checked") != UNKNOWN)).height),
            ],
        ),
    ]


def listing_section(frame: pl.DataFrame, issues: pl.DataFrame) -> list[str]:
    clusters = (
        frame.lazy()
        .filter(pl.col("list_date").is_not_null())
        .group_by("list_date")
        .agg(
            codes=pl.col("code").n_unique(),
            boards=pl.col("board").unique().sort().str.join("/"),
            free=(pl.col("limit_state") == FREE).sum(),
        )
        .filter(pl.col("codes") >= CLUSTER_CODES)
        .sort(["codes", "list_date"], descending=[True, False])
        .collect()
    )
    return [
        "## 2. Free bars by listing date",
        "",
        f"{clusters.height} listing dates are shared by {CLUSTER_CODES} or more codes, with "
        f"{clusters['codes'].sum():,} codes and {clusters['free'].sum():,} FREE bars; the largest {SAMPLE}:",
        "",
        *table(
            ["list date", "codes", "boards", "FREE bars"],
            clusters.head(SAMPLE).select("list_date", "codes", "boards", "free").iter_rows(),
        ),
        *successor_lines(frame, issues),
    ]


def successor_lines(frame: pl.DataFrame, issues: pl.DataFrame) -> list[str]:
    duplicates = issues.filter(pl.col("kind") == "duplicate_code").group_by("code").agg(last=pl.col("date").max())
    if duplicates.height == 0:
        return ["No duplicate_code issues are recorded, so no FREE bar belongs to a successor code.", ""]
    firsts = frame.group_by("code").agg(first=pl.col("date").min())
    successors = firsts.join(duplicates, on="code").filter(pl.col("first") > pl.col("last"))["code"]
    rows = frame.filter(pl.col("code").is_in(successors.implode()) & (pl.col("limit_state") == FREE))
    return [
        f"FREE bars of codes whose first bar follows a recorded duplicate_code issue: {rows.height:,} bars of "
        f"{successors.len()} codes.",
        "",
        *table(
            ["code", "date", "rule", "listing day"],
            rows.head(SAMPLE).select("code", "date", "limit_rule", "listing_day").iter_rows(),
        ),
    ]


def breaches_section(frame: pl.DataFrame, result: CheckResult) -> list[str]:
    breaches = frame.join(result.issues.select("code", "date"), on=["code", "date"], how="semi").with_columns(hint())
    counts = dict(breaches.group_by("hint").len().iter_rows())
    lines = [
        "## 3. Breaches",
        "",
        f"{breaches.height:,} LIMITED bars trade outside their limits. Hints, first match wins: resumed (gap_days > 0), "
        "ex_date (the first bar on or after an adjustment event), preclose_derived (the preclose is computed, not the "
        "exchange's), other.",
        "",
        *table(["hint", "bars"], [(h, counts.get(h, 0)) for h in HINTS]),
        *catch_all(frame),
    ]
    for by, title in (("year", "By year"), ("board", "By board"), ("limit_rule", "By rule")):
        lines += [f"{title}:", "", *by_hint(breaches, by)]
    for name in HINTS:
        sample = breaches.filter(pl.col("hint") == name).head(SAMPLE)
        if sample.height:
            lines += [f"First {sample.height} {name} breaches:", "", *breach_rows(sample)]
    return lines


def catch_all(frame: pl.DataFrame) -> list[str]:
    if not frame["preclose_derived"].all():
        return []
    return [
        "Every bar of this snapshot has a derived preclose, so preclose_derived takes every breach that is neither "
        "resumed nor ex_date, and other cannot occur.",
        "",
    ]


def by_hint(breaches: pl.DataFrame, by: str) -> list[str]:
    frame = breaches.with_columns(year=pl.col("date").dt.year())
    counts = frame.group_by(by, "hint").len()
    rows = []
    for key in sorted(counts[by].unique().to_list()):
        found = dict(counts.filter(pl.col(by) == key).select("hint", "len").iter_rows())
        rows.append((str(key), *(found.get(h, 0) for h in HINTS), sum(found.values())))
    return table([by, *HINTS, "total"], rows)


def breach_rows(sample: pl.DataFrame) -> list[str]:
    columns = ["code", "date", "preclose", "high", "low", "limit_up", "limit_down", "limit_rule", "gap_days"]
    return table(columns, sample.select(columns).iter_rows())


def recorded_section(result: CheckResult, version: int, commit: bool) -> list[str]:
    after = [f"After the commit, v{version} records every breach found and no other (verified).", ""]
    return [
        "## 4. Recorded breaches",
        "",
        f"Compared with the limit_breach rows of data_issues at v{result.version}:",
        "",
        f"- **Missing (found, not recorded):** {result.missing.height:,}",
        f"- **Stale (recorded, no longer found):** {result.stale.height:,}",
        "",
        *(after if commit else []),
    ]


def rules_section(book: RuleBook) -> list[str]:
    rows = [(row.id, row.since, row.confirm) for row in book.unconfirmed()]
    return [
        "## 5. Rules awaiting a primary source",
        "",
        *table(["rule", "since", "to confirm"], rows),
        "Limit rules not modelled; the bars they govern get the normal or ST band and are voided only when they breach "
        "it:",
        "",
        *(f"- {item}" for item in UNMODELLED_LIMITS),
        "",
        "Also not modelled or not sourced:",
        "",
        *(f"- {item}" for item in UNMODELLED_OTHER),
        "",
    ]


def evidence_section(frame: pl.DataFrame, book: RuleBook) -> list[str]:
    limited = pl.col("limit_state") == LIMITED
    at_up, at_down = frame.select(
        up=(limited & (pl.col("close") == pl.col("limit_up"))).sum(),
        down=(limited & (pl.col("low") == pl.col("limit_down"))).sum(),
    ).row(0)
    return [
        "## 6. Evidence",
        "",
        f"- **Closes at the upper limit:** {at_up:,} LIMITED bars.",
        f"- **Lows at the lower limit:** {at_down:,} LIMITED bars.",
        "",
        "Main-board bars of 2007-2010 not known to be ST closing exactly at the half-up ±5% price (S-shares and ST "
        "stocks, whose status the seed lacks), next to closes at the 10% limit:",
        "",
        *five_percent_rows(frame),
        *guard_rows(frame, book),
    ]


def five_percent_rows(frame: pl.DataFrame) -> list[str]:
    pre, bp, tick = pl.col("preclose").cast(pl.Int64), pl.lit(500), pl.lit(10)
    years = (
        frame.lazy()
        .filter((pl.col("limit_state") == LIMITED) & (pl.col("board") == "main") & ~pl.col("is_st").fill_null(False))
        .filter(pl.col("date").dt.year().is_between(2007, 2010))
        .group_by(year=pl.col("date").dt.year().cast(pl.String))
        .agg(
            bars=pl.len(),
            up5=(pl.col("close") == up_price(pre, bp, tick)).sum(),
            down5=(pl.col("close") == down_price(pre, bp, tick)).sum(),
            up10=(pl.col("close") == pl.col("limit_up")).sum(),
        )
        .sort("year")
        .collect()
    )
    return table(["year", "bars", "close at +5%", "close at -5%", "close at the 10% limit"], years.iter_rows())


def guard_rows(frame: pl.DataFrame, book: RuleBook) -> list[str]:
    columns = ["code", "date", "board", "preclose", "is_st", "limit_up", "limit_down", "limit_rule"]
    tick = max(lot.tick for lot in book.lot)
    # A guard leaves a limit one tick from the preclose or at one tick, so only such bars need the exact check.
    near = (
        (pl.col("limit_up") - pl.col("preclose") <= tick)
        | (pl.col("preclose") - pl.col("limit_down") <= tick)
        | (pl.col("limit_down") <= tick)
    )
    candidates = frame.lazy().filter((pl.col("limit_state") == LIMITED) & near).select(columns).collect()
    rows = [row for row in candidates.iter_rows(named=True) if guarded(row, book)]
    return [
        f"Bars where a tick guard moved a limit: {len(rows)}.",
        "",
        *(table(columns, [[row[c] for c in columns] for row in rows]) if rows else []),
    ]


def guarded(row: dict, book: RuleBook) -> bool:
    """True when the row's limits differ from the plain half-up band prices."""
    rule = next(r for r in book.rules() if r.id == row["limit_rule"])
    if isinstance(rule, NewListing):
        up_bp, down_bp = rule.first_up_bp, rule.first_down_bp
    else:
        up_bp = down_bp = rule.st_bp if row["is_st"] else rule.normal_bp
    tick, pre = book.in_force("lot", row["board"], row["date"]).tick, row["preclose"]
    up = round_half_up(pre * (10000 + up_bp), 10000 * tick) * tick
    down = round_half_up(pre * (10000 - down_bp), 10000 * tick) * tick
    return (up, down) != (row["limit_up"], row["limit_down"])


if __name__ == "__main__":
    raise SystemExit(main())
