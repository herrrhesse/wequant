"""Merge the raw BaoStock layer into a store: a dry run reports, --commit writes one version and its report.

python -m tools.baostock_merge copy-store DEST [--home H]
python -m tools.baostock_merge propose [--home H] [--raw R] [--days FIRST..LAST] [--out FILE]
python -m tools.baostock_merge run [--home H] [--raw R] [--days FIRST..LAST] [--resolutions FILE] [--commit]
    [--check-idempotency] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import polars as pl

from wequant.data.merge import MergePlan, apply_merge, plan_merge
from wequant.data.merge.assemble import BJ, read_years, status_to_restore
from wequant.data.merge.audit import FLAGGED, status_disagreements
from wequant.data.merge.conflicts import SEEDED
from wequant.data.merge.detect import FLAGS
from wequant.data.merge.plan import LATE_EVENT_DAYS
from wequant.data.merge.propose import propose
from wequant.data.merge.resolutions import add, default_path, load
from wequant.data.merge.resolve import STATES
from wequant.data.raw import BaoStockRaw
from wequant.data.store import CommitResult, Store, wequant_home
from wequant.version import code_version

WHOLE_TABLES = ("adj_factor", "securities", "data_issues", "calendar")
YEAR_TABLES = ("bars", "daily_status")
FROZEN_TABLES = ("index_bars", "index_members")
FLAGGED_ROWS = 1000  # flagged rows listed in the report at most
LISTED_KINDS = ("preclose_mismatch", "bar_unconfirmed", "tradestatus_conflict", "amount_mismatch")
LISTED_ROWS = 100  # rows listed per kind at most
UNDECIDED_ROWS = 1000  # conflicts without any entry listed at most
LISTED_FILES = 200  # factor files named in the report at most
LISTED_DAYS = 20  # days to re-merge named at most
KEY = ["code", "date"]
DETAIL_VALUES = ("before", "baostock", "after")  # a resolved row's values, in report order


def copy_store(source: Path, target: Path) -> int:
    """Copy the current version of the store at source to target, without its lock or raw layer; files copied."""
    text = (source / "manifest.json").read_text("utf-8")
    manifest = json.loads(text)
    paths = [e["path"] for table in manifest["tables"].values() for e in table["files"]]
    paths.append(f"manifests/manifest.v{manifest['version']}.json")
    for rel in paths:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, target / rel)
    (target / "manifest.json").write_text(text, "utf-8")  # last, so an interrupted copy is never a store
    return len(paths) + 1


def raw_days(raw: BaoStockRaw, spec: str | None) -> list[dt.date] | None:
    """The raw days on disk in FIRST..LAST (inclusive), or None for every day."""
    if spec is None:
        return None
    first, last = (dt.date.fromisoformat(part) for part in spec.split(".."))
    return sorted(d for d in raw.daily_days() if first <= d <= last)


def run(args: argparse.Namespace) -> int:
    """Plan, report and, with --commit, apply one merge; 1 when a conflict lacks an approved resolution (nothing is
    committed then) or an acceptance criterion fails."""
    store = Store(args.home)
    raw = BaoStockRaw(Path(args.raw)) if args.raw else BaoStockRaw.at(store.home)
    plan = plan_merge(store, raw, days=raw_days(raw, args.days), resolutions=args.resolutions)
    stopped = plan.unresolved.height > 0
    commit, checks = None, {}
    if args.commit and not stopped:
        commit = apply_merge(store, plan)
        for frames in (plan.replace_parts, plan.replace, plan.upsert):
            frames.clear()  # committed; the report and checks need only the stats and issues, and memory is tight
        checks = acceptance(store, plan, commit)
        if args.check_idempotency:
            checks["idempotent"] = idempotent(store, raw, plan, args.resolutions)
    report = render(plan, commit, checks, args.resolutions or default_path(store.home))
    targets = [Path(args.out)] if args.out else []
    if commit is not None:
        name = f"baostock_merge_{store.manifest()['last_trade_date']}_v{commit.version}.md"
        targets.append(store.home / "reports" / name)
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, "utf-8")
    if not targets:
        sys.stdout.write(report)
    return 0 if not stopped and all(ok for ok, _ in checks.values()) else 1


def propose_entries(args: argparse.Namespace) -> int:
    """Add a proposed entry for each conflict on the raw days to the resolutions file, whose price entries the amount
    check follows; reads the store and raw only."""
    store = Store(args.home)
    raw = BaoStockRaw(Path(args.raw)) if args.raw else BaoStockRaw.at(store.home)
    path = args.out or default_path(store.home)
    entries = load(path) if path.exists() else None
    proposals = propose(store, raw, days=raw_days(raw, args.days), entries=entries)
    added = add(path, proposals)
    print(f"{len(proposals)} proposals; {added} added to {path}, {len(proposals) - added} already there")
    counts = Counter((p["kind"], p["take"], ", ".join(p["fields"])) for p in proposals)
    for (kind, take, fields), n in sorted(counts.items()):
        print(f"  {kind} take {take} [{fields}]: {n}")
    return 0


def idempotent(store: Store, raw: BaoStockRaw, plan: MergePlan, resolutions: Path | None) -> tuple[bool, str]:
    """Plan and apply the same days and factor files again; nothing may change."""
    again = apply_merge(
        store, plan_merge(store, raw, days=plan.days, factor_codes=plan.factor_codes, resolutions=resolutions)
    )
    ok = not again.changed and not again.files_written
    return ok, f"re-run of the same days and factor files: changed {again.changed}, files written {again.files_written}"


# ------------------------------------------------------------ acceptance


def acceptance(store: Store, plan: MergePlan, commit: CommitResult) -> dict[str, tuple[bool, str]]:
    """Each acceptance criterion of the merge: (passed, what was measured)."""
    manifest = store.manifest()
    return {
        "resolutions_applied": resolutions_applied(store, manifest, plan),
        "status_agrees": status_agrees(store, manifest, plan),
        "adjustment_checked": adjustment_checked(store, manifest, plan),
        "factor_files_consistent": factor_files_consistent(plan),
        "touched_only": touched_only(plan, commit, manifest),
        "registered": registered(store, manifest),
        "status_complete": status_complete(store, manifest),
        "bj_frozen": bj_frozen(store, store.manifest(plan.base_version), manifest),
    }


def resolutions_applied(store: Store, manifest: dict, plan: MergePlan) -> tuple[bool, str]:
    """Each committed bar a resolution decided holds that resolution's after values."""
    resolved = _resolved(plan.issues)
    if resolved.is_empty():
        return True, "no resolution applied in this run"
    span = {"start": resolved["date"].min(), "end": resolved["date"].max(), "manifest": manifest}
    bars = store.scan("bars", **span).join(resolved.lazy().select(KEY), on=KEY, how="semi").collect()
    committed = {(row["code"], row["date"]): row for row in bars.to_dicts()}
    differ = [
        f"{code} {day}"
        for code, day, detail in resolved.select("code", "date", "detail").rows()
        if any(committed.get((code, day), {}).get(f, ...) != v for f, v in json.loads(detail)["after"].items())
    ]
    return not differ, (
        f"{resolved.height} resolutions applied; committed bars differing from their after values: "
        f"{', '.join(differ[:LISTED_DAYS]) or 'none'}"
    )


def _resolved(issues: pl.DataFrame) -> pl.DataFrame:
    return issues.filter(pl.col("action").str.starts_with("resolved_")).sort("date", "code", "kind")


def status_agrees(store: Store, manifest: dict, plan: MergePlan) -> tuple[bool, str]:
    """On merged days: status disagreements are exactly the tradestatus_conflict issues, and bars repeat the status."""
    on_days = pl.col("date").is_in(plan.days) & ~BJ
    span = {"start": plan.days[0], "end": plan.days[-1], "manifest": manifest}
    columns = [*KEY, "volume", "tradestatus", "is_st"]
    bars = store.scan("bars", columns=columns, **span).filter(on_days).collect()
    status = store.scan("daily_status", **span).filter(on_days).collect()
    found = set(status_disagreements(bars, status).select(KEY).rows())
    issues = store.scan("data_issues", **span).filter(on_days & (pl.col("kind") == "tradestatus_conflict"))
    recorded = set(issues.select(KEY).collect().rows())
    unlisted = bars.join(status, on=KEY, how="anti").height
    differ = _not_repeated(bars, status)
    ok = found == recorded and unlisted == 0 and differ == 0
    return (
        ok,
        f"{len(found)} disagreements, {len(recorded)} tradestatus_conflict issues, {unlisted} bars without status, "
        f"{differ} bars whose tradestatus or is_st differ from daily_status",
    )


def _not_repeated(bars: pl.DataFrame, status: pl.DataFrame) -> int:
    """Bars whose tradestatus or is_st differ from their daily_status row."""
    joined = bars.join(status, on=KEY, suffix="_status")
    same = [pl.col(c).eq_missing(pl.col(f"{c}_status")) for c in ("tradestatus", "is_st")]
    return joined.filter(~pl.all_horizontal(same)).height


def adjustment_checked(store: Store, manifest: dict, plan: MergePlan) -> tuple[bool, str]:
    """Every committed exchange-preclose bar after a code's first bar was checked; flagged counts for the user."""
    bars = store.scan("bars", columns=["code", "date", "preclose_derived"], manifest=manifest).filter(~BJ)
    first = pl.col("date") == pl.col("date").min().over("code")
    wanted = bars.filter(~pl.col("preclose_derived") & ~first).select(pl.len()).collect().item()
    stats = plan.stats["adjustment"]
    flagged = ", ".join(f"{k} {stats['flagged'].get(k, 0)}" for k in FLAGGED)
    ok = wanted == stats["checked"]
    return ok, f"{stats['checked']:,} of {wanted:,} exchange-preclose bars checked; flagged: {flagged}"


def factor_files_consistent(plan: MergePlan) -> tuple[bool, str]:
    if not plan.factor_codes:
        return True, "no factor files in this run"
    flagged = plan.issues.filter(pl.col("kind").is_in(FLAGGED) & pl.col("code").is_in(plan.factor_codes))
    return flagged.is_empty(), f"{flagged.height} flagged rows for the {len(plan.factor_codes)} codes with factor files"


def touched_only(plan: MergePlan, commit: CommitResult, manifest: dict) -> tuple[bool, str]:
    """Only the touched years and the merge's whole tables are written; every other part keeps its file."""
    years = plan.stats["years"]
    touched = {f"{t}/{y}" for t in YEAR_TABLES for y in years[t]} | {f"{t}/{t}" for t in WHOLE_TABLES}
    stray = [p for p in commit.files_written if _part(p) not in touched]
    tables = (*YEAR_TABLES, *FROZEN_TABLES)
    others = {f"{t}/{e['part']}" for t in tables for e in manifest["tables"].get(t, {}).get("files", [])} - touched
    unkept = sorted(others - {_part(p) for p in commit.files_kept})
    return not stray and not unkept, (
        f"{len(commit.files_written)} files written; outside the touched parts: {stray or 'none'}; "
        f"other parts not kept: {unkept or 'none'}"
    )


def _part(path: str) -> str:
    """bars/2024.v5.parquet -> bars/2024."""
    return path.rsplit(".v", 1)[0]


def registered(store: Store, manifest: dict) -> tuple[bool, str]:
    problems = invariant_problems(store, manifest)
    return not problems, "; ".join(problems) or "every store invariant holds"


def invariant_problems(store: Store, manifest: dict) -> list[str]:
    """Breaches of the store invariants at one manifest: codes known to securities, SH/SZ spans equal to the bars,
    daily_status only for SH/SZ codes from their first bar."""
    securities = store.read_table("securities", manifest=manifest)
    bars = store.scan("bars", columns=KEY, manifest=manifest)
    spans = bars.group_by("code").agg(first=pl.col("date").min(), last=pl.col("date").max()).collect()
    problems = [_unknown_codes(store, manifest, table, securities) for table in ("bars", "adj_factor", "daily_status")]
    problems += [_span_problem(securities, spans), _status_problem(store, manifest, spans)]
    return [p for p in problems if p]


def _unknown_codes(store: Store, manifest: dict, table: str, securities: pl.DataFrame) -> str:
    codes = store.scan(table, columns=["code"], manifest=manifest).unique().collect()
    unknown = codes.join(securities, on="code", how="anti")["code"].sort()
    if unknown.is_empty():
        return ""
    return f"{table} has {unknown.len()} codes without a securities row: {unknown.head(10).to_list()}"


def _span_problem(securities: pl.DataFrame, spans: pl.DataFrame) -> str:
    joined = securities.filter(~BJ).join(spans, on="code", how="left")
    same = pl.col("first_date").eq_missing(pl.col("first")) & pl.col("last_date").eq_missing(pl.col("last"))
    off = joined.filter(~same)["code"]
    return f"{off.len()} SH/SZ spans differ from their bars: {off.head(10).to_list()}" if off.len() else ""


def _status_problem(store: Store, manifest: dict, spans: pl.DataFrame) -> str:
    status = store.scan("daily_status", columns=KEY, manifest=manifest).join(spans.lazy(), on="code", how="left")
    bad = status.filter(BJ | pl.col("first").is_null() | (pl.col("date") < pl.col("first")))
    rows = bad.select(pl.len()).collect().item()
    return f"{rows} daily_status rows are BJ or before the code's first bar" if rows else ""


def status_complete(store: Store, manifest: dict) -> tuple[bool, str]:
    """No status row an earlier merge dropped before a first bar lies on or after the code's first bar now."""
    spans = store.read_table("securities", manifest=manifest).filter(~BJ)
    restore = status_to_restore(store, manifest, store.read_table("data_issues", manifest=manifest), spans)
    if restore.is_empty():
        return True, "every status row dropped before a first bar is still before it"
    days = sorted(set(restore["date"]))
    return False, f"{restore.height} dropped status rows are now on or after the first bar; {_remerge(days)}"


def _remerge(days: list[dt.date]) -> str:
    shown = ", ".join(str(d) for d in days[:LISTED_DAYS]) + (", ..." if len(days) > LISTED_DAYS else "")
    return f"re-merge {len(days)} days: {shown}"


def bj_frozen(store: Store, base: dict, manifest: dict) -> tuple[bool, str]:
    """BJ bars, securities and adj_factor rows equal the base version's, and BJ has no status rows."""
    years = sorted({int(e["part"]) for m in (base, manifest) for e in m["tables"]["bars"]["files"]})
    counts, same = {}, True
    for year in years:
        before, after = (read_years(store, m, "bars", [year]).filter(BJ).sort("code", "date") for m in (base, manifest))
        same &= before.equals(after)
        counts[year] = (before.height, after.height)
    for table, key in (("securities", "code"), ("adj_factor", ["code", "ex_date"])):
        before, after = (store.read_table(table, manifest=m).filter(BJ).sort(key) for m in (base, manifest))
        same &= before.equals(after)
    status = store.scan("daily_status", columns=["code"], manifest=manifest).filter(BJ).select(pl.len()).collect()
    rows = sum(a for _, a in counts.values())
    return same and status.item() == 0, f"{rows:,} BJ bars in {len(counts)} years; {status.item()} BJ status rows"


# ------------------------------------------------------------ report


def render(plan: MergePlan, commit: CommitResult | None, checks: dict[str, tuple[bool, str]], resolutions: Path) -> str:
    """The Markdown report of one merge."""
    sections = [
        _header(plan, commit, resolutions),
        _rows_section(plan),
        _issues_section(plan),
        _listed_section(plan),
        _resolutions_section(plan),
        _awaiting_section(plan),
        _adjustment_section(plan),
        _late_events_section(plan),
        _unconfirmed_events_section(plan),
        _bj_section(plan),
        _acceptance_section(checks),
        _inputs_section(plan),
    ]
    return "\n".join(line for section in sections for line in section) + "\n"


def table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(_cell(x) for x in row) + " |" for row in rows] + [""]


def _cell(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int):
        return f"{value:,}"
    return "" if value is None else str(value)


def _header(plan: MergePlan, commit: CommitResult | None, resolutions: Path) -> list[str]:
    if commit is None:
        version = f"dry run on version {plan.base_version}; nothing written"
    else:
        version = f"version {plan.base_version} → {commit.version}" + ("" if commit.changed else " (unchanged)")
    counts = plan.stats["resolutions"]
    return [
        "# BaoStock merge",
        "",
        *_stop_line(counts),
        f"- **Store:** {version}",
        f"- **Resolutions:** {resolutions}: {sum(counts['applied'].values()):,} applied, "
        f"{sum(counts['unresolved'].values()):,} conflicts awaiting a decision, {len(counts['idle']):,} approved "
        "entries met no conflict",
        f"- **code_version:** {code_version()}",
        f"- **Days:** {plan.days[0]}..{plan.days[-1]} ({len(plan.days):,} raw days); factor files: {len(plan.factor_codes):,}",
        f"- **Meta after the merge:** {json.dumps(plan.meta, sort_keys=True)}",
        *(
            [
                f"- **Files:** {len(commit.files_written)} written, {len(commit.files_reused)} reused, "
                f"{len(commit.files_kept)} kept: {', '.join(commit.files_written) or 'none written'}"
            ]
            if commit
            else []
        ),
        "",
    ]


def _stop_line(counts: dict) -> list[str]:
    waiting = counts["unresolved"]
    if not sum(waiting.values()):
        return []
    return [
        f"- **Stop:** {sum(waiting.values()):,} conflicts lack an approved resolution ({waiting['missing']:,} without an "
        f"entry, {waiting['unfetched']:,} waiting for their reference day, {waiting['proposed']:,} with a proposed "
        f"entry awaiting approval, {waiting['rejected']:,} with a rejected entry, {waiting['unpriced']:,} amount "
        "conflicts waiting for their cell's price entry); "
        "nothing is committed until each has one (section 5).",
    ]


def _rows_section(plan: MergePlan) -> list[str]:
    s = plan.stats
    merged, pre, gaps, turn, layout = s["merged"], s["preclose"], s["seed_gaps"], s["turn"], s["layout_only"]
    quantiles = [(name, *(s[name][q] for q in ("p50", "p99", "max"))) for name in ("volume_rel", "amount_rel")]
    return [
        "## 1. Rows",
        "",
        *table(["table", "rows after the merge"], s["rows"].items()),
        f"- **Merged:** {merged['bars']:,} bars and {merged['daily_status']:,} status rows "
        f"({merged['suspended']:,} suspended, {merged['st']:,} ST).",
        f"- **Touched years:** bars {s['years']['bars']}, daily_status {s['years']['daily_status']}.",
        f"- **Stale layouts rewritten:** {s['years']['stale'] or 'none'}; {len(layout['years'])} of these years "
        f"({layout['rows']:,} rows) have no merged day and are rewritten only to gain the current columns.",
        f"- **turn:** {turn['non_null']:,} of {turn['bars']:,} merged BaoStock bars have a turnover "
        f"({_share(turn['non_null'], turn['bars'])}); {turn['null_traded']:,} merged bars trade without one.",
        f"- **Compared with the store:** {s['compared']:,} bars; derived precloses replaced by the exchange's: "
        f"{pre['replaced']:,}, of which {pre['differing']:,} differ.",
        f"- **Seed gaps** (listed days inside the store span without a store bar): {gaps['tradestatus_0']:,} with "
        f"tradestatus 0, {gaps['traded']:,} traded.",
        _restore_line(s["status_to_restore"]),
        _adj_line(s["adj_factor"]),
        "",
        *table(["relative error", "p50", "p99", "max"], quantiles),
    ]


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.4%}" if whole else "no bars"


def _restore_line(restore: dict) -> str:
    if not restore["rows"]:
        return "- **Status rows to restore:** none."
    return (
        f"- **Status rows to restore:** {restore['rows']:,} rows an earlier merge dropped before a first bar that has "
        f"since moved earlier; {_remerge(restore['days'])}."
    )


def _adj_line(changes: dict) -> str:
    if not changes["rewritten"]:
        return "- **adj_factor:** unchanged."
    return (
        f"- **adj_factor:** rewritten; codes removed: {_codes(changes['removed'])}; "
        f"first row moved: {_codes(changes['moved'])}."
    )


def _codes(codes: list[str]) -> str:
    return f"{len(codes)} ({', '.join(codes[:20])}{', ...' if len(codes) > 20 else ''})" if codes else "none"


def _issues_section(plan: MergePlan) -> list[str]:
    rows = []
    for (kind,), group in plan.issues.group_by("kind", maintain_order=True):
        dates = group.group_by("date").len().sort(["len", "date"], descending=[True, False])
        codes = group.group_by("code").len().sort(["len", "code"], descending=[True, False])
        rows.append(
            (
                kind,
                group.height,
                dates.height,
                _busiest(dates.head(5)),
                codes.height,
                _busiest(codes.head(5)),
            )
        )
    return [
        "## 2. Issues of this run",
        "",
        *(
            table(["kind", "rows", "dates", "busiest dates", "codes", "busiest codes"], sorted(rows))
            if rows
            else ["None.", ""]
        ),
    ]


def _listed_section(plan: MergePlan) -> list[str]:
    lines = ["## 3. Value and status disagreements", ""]
    for kind in LISTED_KINDS:
        rows = plan.issues.filter(pl.col("kind") == kind).sort("date", "code")
        if rows.height:
            shown = rows.head(LISTED_ROWS).select("code", "date", "action", "detail").rows()
            more = f", the first {LISTED_ROWS} listed" if rows.height > LISTED_ROWS else ""
            lines += [
                f"**{kind}:** {rows.height:,} rows{more}.",
                "",
                *table(["code", "date", "action", "detail"], shown),
            ]
    return lines if len(lines) > 2 else [*lines, "None.", ""]


def _resolutions_section(plan: MergePlan) -> list[str]:
    resolved = _resolved(plan.issues)
    details = [json.loads(d) for d in resolved["detail"]]
    rows = [
        (code, day, kind, action, ", ".join(d["fields"]), *(_ordered(d, x) for x in DETAIL_VALUES), d["evidence"])
        for (code, day, kind, action), d in zip(resolved.select("code", "date", "kind", "action").rows(), details)
    ]
    header = ["code", "date", "kind", "action", "fields", "store before", "BaoStock", "after", "evidence"]
    return ["## 4. Resolutions applied", "", *(table(header, rows) if rows else ["None.", ""]), *_detections(plan)]


def _detections(plan: MergePlan) -> list[str]:
    """The days whose snapshot looks cut against the seed, the rows each detection marked, and the close flags."""
    found = plan.stats["detections"]
    days = [(d["date"], d["exchange"], d["rows"], d["short"], d["share"]) for d in found["truncated_days"]]
    lines = [
        f"**Detections:** {found['stale']:,} suspended rows with a stale volume, {found['carried']:,} amounts "
        f"repeating the previous bar, {found['truncated']:,} volumes short of the seed's on {len(days)} cut "
        "exchange-days; each is a conflict unless the seed agrees (amounts).",
        "",
        *(table(["date", "exchange", "rows with a seed bar", "short", "share"], days) if days else []),
    ]
    for kind in FLAGS:
        rows = plan.issues.filter(pl.col("kind") == kind).sort("date", "code")
        shown = [(c, day, d) for c, day, d in rows.head(LISTED_ROWS).select(*KEY, "detail").rows()]
        more = f", the first {LISTED_ROWS} listed" if rows.height > LISTED_ROWS else ""
        lines += [f"**{kind}:** {rows.height:,} rows flagged{more}.", ""]
        lines += table(["code", "date", "detail"], shown) if shown else []
    return lines


def _awaiting_section(plan: MergePlan) -> list[str]:
    """Conflicts without an approved entry, those without any entry first, and approved entries that met none."""
    lines = ["## 5. Conflicts awaiting a decision", ""]
    rows = plan.unresolved
    if rows.height:
        caps = {
            "missing": UNDECIDED_ROWS,
            "unfetched": UNDECIDED_ROWS,
            "proposed": LISTED_ROWS,
            "rejected": LISTED_ROWS,
            "unpriced": LISTED_ROWS,
        }
        shown = pl.concat(rows.filter(pl.col("entry") == state).head(cap) for state, cap in caps.items())
        lines += [
            f"The merge stops before writing anything: {rows.height:,} conflicts lack an approved entry in the "
            f"resolutions file. Listed: those without an entry (at most {UNDECIDED_ROWS:,}), then those with a proposed "
            f"one and the amount conflicts waiting for a price entry (at most {LISTED_ROWS} each). `propose` adds "
            "missing entries as proposed; only the user approves them.",
            "",
            *table(["code", "date", "kind", "entry", "BaoStock", "store"], [_awaiting(r) for r in shown.to_dicts()]),
        ]
    idle = plan.stats["resolutions"]["idle"]
    if idle:
        lines += [
            f"**Approved entries that met no conflict:** {len(idle):,}. They take other than BaoStock's values, but the "
            "bars keep BaoStock's: the conflict is gone (the raw row changed), or the entry changed after the store "
            "took an earlier one.",
            "",
            *table(["code", "date", "kind", "fields", "take"], [_idle(entry) for entry in idle]),
        ]
    return lines if len(lines) > 2 else [*lines, "None.", ""]


def _awaiting(row: dict) -> tuple:
    baostock, store = ({c: row[f"{prefix}{c}"] for c in SEEDED} for prefix in ("", "seed_"))
    return row["code"], row["date"], row["kind"], STATES[row["entry"]], _values(baostock), _values(store)


def _idle(entry: dict) -> tuple:
    return entry["code"], entry["date"], entry["kind"], ", ".join(entry["fields"]), entry["take"]


def _ordered(detail: dict, which: str) -> str:
    """detail's values of one source in the order of its fields; 'no store bar' for a before without one."""
    values = detail[which]
    return "no store bar" if values is None else _values({f: values[f] for f in detail["fields"] if f in values})


def _values(values: dict) -> str:
    return ", ".join(f"{name} {value}" for name, value in values.items())


def _busiest(counts: pl.DataFrame) -> str:
    return ", ".join(f"{key} ({n:,})" for key, n in counts.rows())


def _adjustment_section(plan: MergePlan) -> list[str]:
    s = plan.stats["adjustment"]
    flagged = plan.issues.filter(pl.col("kind").is_in(FLAGGED)).sort("code", "date")
    details = [json.loads(d) for d in flagged["detail"].head(FLAGGED_ROWS)]
    columns = ["prev_date", "prev_close", "preclose", "implied_step", "factor_step", "ex_date"]
    rows = [
        (c, day, k, *(d[x] for x in columns))
        for (c, day, k), d in zip(flagged.select("code", "date", "kind").rows(), details, strict=False)
    ]
    return [
        "## 6. Adjustment check",
        "",
        f"{s['checked']:,} exchange-preclose bars checked against adj_factor ({s['agree']:,} agree); "
        f"{s['first_bar']:,} first bars have no earlier close. Flagged: "
        + (", ".join(f"{k} {n:,}" for k, n in s["flagged"].items()) or "none")
        + ".",
        "",
        *(table(["code", "date", "kind", *columns], rows) if rows else []),
        *(
            [f"First {FLAGGED_ROWS} of {flagged.height} flagged rows shown.", ""]
            if flagged.height > FLAGGED_ROWS
            else []
        ),
    ]


def _late_events_section(plan: MergePlan) -> list[str]:
    events = plan.stats["adjustment"]["late_seed_events"]
    rows = [(e["code"], e["missing"], e["unimplied"], e["lag"], e["step"]) for e in events]
    return [
        "## 7. Late seed events",
        "",
        f"An event_missing followed within {LATE_EVENT_DAYS} trading days by an event_unimplied of the same step: "
        "the seed placed the event late.",
        "",
        *(
            table(["code", "exchange ex-day", "seed ex-day", "trading days late", "step"], rows)
            if rows
            else ["None.", ""]
        ),
    ]


def _unconfirmed_events_section(plan: MergePlan) -> list[str]:
    dropped = plan.issues.filter(pl.col("kind") == "event_unconfirmed").group_by("date").len().sort("date")
    return [
        "## 8. Store events the factor files do not confirm",
        "",
        *(table(["date", "event_unconfirmed"], dropped.rows()) if dropped.height else ["None.", ""]),
    ]


def _bj_section(plan: MergePlan) -> list[str]:
    rows = [(str(year), before, after) for year, (before, after) in plan.stats["bj_rows"].items()]
    return ["## 9. BJ bars in the touched years", "", *table(["year", "before", "after"], rows)]


def _acceptance_section(checks: dict[str, tuple[bool, str]]) -> list[str]:
    if not checks:
        return ["## 10. Acceptance", "", "Runs with --commit.", ""]
    rows = [(name, "pass" if ok else "FAIL", message) for name, (ok, message) in checks.items()]
    return ["## 10. Acceptance", "", *table(["criterion", "result", "measured"], rows)]


def _inputs_section(plan: MergePlan) -> list[str]:
    files = ", ".join(plan.factor_codes[:LISTED_FILES]) or "none"
    more = f" and {len(plan.factor_codes) - LISTED_FILES:,} more" if len(plan.factor_codes) > LISTED_FILES else ""
    return [
        "## 11. Inputs of this run",
        "",
        f"- **Raw days:** {len(plan.days):,}, {plan.days[0]}..{plan.days[-1]}.",
        f"- **Factor files:** {files}{more}.",
        "",
    ]


# ------------------------------------------------------------ command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.baostock_merge", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    copy = commands.add_parser("copy-store", help="copy the current store version, without lock or raw layer")
    copy.add_argument("dest", type=Path)
    copy.add_argument("--home", type=Path, default=None)
    proposing = commands.add_parser("propose", help="add a proposed resolution for each conflict; reads only")
    merge = commands.add_parser("run", help="plan the merge, report it, and commit with --commit")
    for command in (proposing, merge):
        command.add_argument("--home", type=Path, default=None)
        command.add_argument("--raw", type=Path, default=None, help="raw BaoStock root (default: <home>/raw/baostock)")
        command.add_argument("--days", default=None, help="FIRST..LAST, inclusive ISO dates (default: every raw day)")
    default = "resolutions file (default: <home>/resolutions/baostock.toml)"
    proposing.add_argument("--out", type=Path, default=None, help=default)
    merge.add_argument("--resolutions", type=Path, default=None, help=default)
    merge.add_argument("--commit", action="store_true")
    merge.add_argument("--check-idempotency", action="store_true", help="re-plan and re-apply; needs --commit")
    merge.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    args.home = args.home or wequant_home()
    if args.command == "copy-store":
        print(f"copied {copy_store(Path(args.home), args.dest)} files to {args.dest}")
        return 0
    if args.command == "propose":
        return propose_entries(args)
    if args.check_idempotency and not args.commit:
        parser.error("--check-idempotency needs --commit")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
