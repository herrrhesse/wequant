"""plan_merge decides every write of one merge from the store and the raw layer; apply_merge commits it as one version."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from wequant.data import codes as codemod
from wequant.data.issues import issue_rows
from wequant.data.merge.assemble import (
    BJ,
    adj_factor_table,
    bars_after,
    data_issues_table,
    orphan_status_years,
    part_rows,
    previous_bars,
    read_years,
    securities_table,
    stale_years,
    status_to_restore,
)
from wequant.data.merge.audit import (
    FLAGGED,
    STEP_RTOL,
    audit_bars,
    audit_events,
    check_adjustment,
    status_disagreements,
)
from wequant.data.merge.conflicts import price_conflicts, with_references
from wequant.data.merge.daily import read_daily, split
from wequant.data.merge.detect import MARKS, close_flags, marks, truncated_days
from wequant.data.merge.factors import events, normalize, read_factors
from wequant.data.merge.reconcile import drop_duplicate_codes, drop_status_before_first_bar
from wequant.data.merge.resolutions import default_path
from wequant.data.merge.resolutions import load as load_resolutions
from wequant.data.merge.resolve import UnresolvedConflicts, first_before, idle_entries, resolve, state_counts
from wequant.data.raw import BaoStockRaw
from wequant.data.store import CommitResult, Store
from wequant.version import code_version

KEY = ["code", "date"]
STORED = ("calendar", "securities", "adj_factor", "data_issues")  # whole tables every merge reads
LATE_EVENT_DAYS = 250  # trading days within which a seed event placed late is paired with the real one


@dataclass
class MergePlan:
    base_version: int
    days: list[dt.date]
    factor_codes: list[str]
    replace_parts: dict[str, pl.DataFrame]  # "bars", "daily_status": every row of each touched year
    replace: dict[str, pl.DataFrame]  # "securities", "data_issues"; "adj_factor" only when its rows change
    upsert: dict[str, pl.DataFrame]  # "calendar"
    issues: pl.DataFrame  # this run's record and flagged rows
    unresolved: pl.DataFrame  # conflicts without an approved resolution: apply_merge refuses the plan
    stats: dict
    meta: dict  # last_trade_date, baostock_merge {first_day, last_day, days}


class StalePlan(RuntimeError):
    """The store committed a new version after the plan was made."""


def plan_merge(
    store: Store,
    raw: BaoStockRaw,
    days: Iterable[dt.date] | None = None,
    factor_codes: Iterable[str] | None = None,
    resolutions: Path | None = None,
) -> MergePlan:
    """What one merge of raw snapshots (None: every day on disk) and factor files (None: every file) writes, with the
    approved entries of a resolutions file (None: the data root's) applied; reads only."""
    manifest = store.manifest()
    if manifest is None:
        raise ValueError(f"{store.home} has no committed version to merge into")
    entries = load_resolutions(resolutions or default_path(store.home))
    stored = {table: store.read_table(table, manifest=manifest) for table in STORED}
    calendar, stored_adj = stored["calendar"]["date"], stored["adj_factor"]
    days = _merge_days(raw, days, calendar)
    factor_codes = _factor_codes(raw, factor_codes)
    bars_new, status_new = split(read_daily(raw, days))
    source = _newest(status_new["source"])

    stale = stale_years(store, manifest, "bars")
    years = {"bars": {d.year for d in days} | stale}
    previous = previous_bars(store, manifest, days[0])
    parts, bars_new, status_new, bar_issues, unresolved, bar_stats = _merge_bars(
        store, manifest, raw, stored, entries, years["bars"], days, bars_new, status_new, previous, source
    )
    after = bars_after(store, manifest, parts, years["bars"])
    spans = after.group_by("code").agg(first_date=pl.col("date").min(), last_date=pl.col("date").max())
    years["daily_status"] = {d.year for d in days} | orphan_status_years(store, manifest, spans)
    status_new, status_parts, early = _merge_status(store, manifest, status_new, spans, years, days, source)

    new_adj = normalize(read_factors(raw, factor_codes), spans.filter(pl.col("code").is_in(factor_codes)))
    event_issues, event_stats = _event_issues(new_adj, stored_adj, factor_codes, after, source)
    securities_new, no_bars = securities_table(stored["securities"], spans, source)
    adj = adj_factor_table(stored_adj, new_adj, factor_codes, spans)
    flagged, checked = check_adjustment(after, adj.filter(~BJ), source)
    flagged = pl.concat([flagged, close_flags(checked, after, source)])
    conflicts = _conflict_issues(status_disagreements(bars_new, status_new), source)

    record = pl.concat([bar_issues, early, event_issues, no_bars, conflicts])
    issues = pl.concat([record, flagged])
    replace_parts = _checked_parts(manifest, {"bars": parts, "daily_status": status_parts}, years)
    replace = _whole_tables(securities_new, data_issues_table(stored["data_issues"], record, flagged), adj, stored_adj)
    full_calendar = pl.concat([calendar, pl.Series("date", days)]).unique().sort()
    restore = status_to_restore(store, manifest, stored["data_issues"], spans).filter(~pl.col("date").is_in(days))
    stats = {
        "rows": _rows(manifest, replace_parts, years, replace | {"adj_factor": adj, "calendar": full_calendar}),
        "merged": _merged_counts(bars_new, status_new),
        "years": {table: sorted(y) for table, y in years.items()} | {"stale": sorted(stale)},
        "layout_only": _layout_only(manifest, stale - {d.year for d in days}),
        **bar_stats,
        "events": event_stats,
        "adj_factor": _adj_changes(stored_adj.filter(~BJ), adj.filter(~BJ), "adj_factor" in replace),
        "adjustment": _adjustment_stats(after, checked, flagged, full_calendar),
        "issues": _counts(issues, "kind"),
        "status_to_restore": {"rows": restore.height, "days": sorted(set(restore["date"]))},
    }
    return MergePlan(
        base_version=manifest["version"],
        days=days,
        factor_codes=factor_codes,
        replace_parts=replace_parts,
        replace=replace,
        upsert={"calendar": pl.DataFrame({"date": days}, schema={"date": pl.Date})},
        issues=issues,
        unresolved=unresolved,
        stats=stats,
        meta=_meta(store, manifest, status_parts, years["daily_status"], full_calendar),
    )


def apply_merge(store: Store, plan: MergePlan, timeout: float = 0, reason: str = "baostock_merge") -> CommitResult:
    """Commit the plan, code_version added to its block, as one version under the writer lock, made for reason
    (daily_update: its days only); StalePlan if the store moved on, UnresolvedConflicts while a conflict lacks an
    approved resolution."""
    if plan.unresolved.height:
        raise UnresolvedConflicts(plan.unresolved)
    with store.lock(timeout):
        current = store.manifest()["version"]
        if current != plan.base_version:
            raise StalePlan(f"planned on version {plan.base_version}, the store is at version {current}")
        block = {**plan.meta["baostock_merge"], "code_version": code_version()}
        applied = sum(plan.stats["resolutions"]["applied"].values())
        params = {"days": len(plan.days)} | ({} if reason == "daily_update" else {"resolutions": applied})
        return store.commit(
            {**plan.meta, "baostock_merge": block},
            replace_parts=plan.replace_parts,
            replace=plan.replace,
            upsert=plan.upsert,
            reason=reason,
            reason_params=params,
        )


def _merge_days(raw: BaoStockRaw, days: Iterable[dt.date] | None, calendar: pl.Series) -> list[dt.date]:
    days = sorted(set(raw.daily_days() if days is None else days))
    if not days:
        raise ValueError("no raw day to merge")
    absent = [d for d in days if not raw.daily(d).exists()]
    if absent:
        raise ValueError(f"no raw snapshot for {len(absent)} days: {_listed(absent)}")
    holes = calendar.filter(calendar.is_between(days[0], days[-1]) & ~calendar.is_in(days))
    if holes.len():
        raise ValueError(f"the days skip {holes.len()} store calendar days: {_listed(holes.to_list())}")
    return days


def _factor_codes(raw: BaoStockRaw, factor_codes: Iterable[str] | None) -> list[str]:
    found = sorted(set(raw.adjust_factor_codes() if factor_codes is None else factor_codes))
    beijing = [c for c in found if codemod.split(c)[1] == "BJ"]
    if beijing:
        raise ValueError(f"factor files must be SH/SZ: {_listed(beijing)}")
    return found


def _listed(values: list) -> str:
    return ", ".join(str(v) for v in values[:20]) + (", ..." if len(values) > 20 else "")


def _newest(sources: pl.Series) -> str:
    """The source with the highest version among source@version strings."""
    return max(sources.unique().to_list(), key=lambda s: [int(n) for n in re.findall(r"[0-9]+", s)])


def _merge_bars(store, manifest, raw, stored, entries, years, days, bars_new, status_new, previous, source) -> tuple:
    """(bars parts of the touched years, new bars, new status rows, issues, conflicts without an approved entry, stats):
    duplicate codes dropped, the approved resolutions applied and the store's bars on audited days compared."""
    old, kept, bj_rows = _stored_bars(store, manifest, years, days)
    bars_new, status_new, duplicates = drop_duplicate_codes(bars_new, status_new, old, source)
    found, cut = marks(bars_new, status_new, previous, old), truncated_days(bars_new, old)  # as BaoStock delivered them
    bars_new, resolved, unresolved, counts = _resolve(raw, stored, entries, days, bars_new, old, found, source)
    calendar, securities = stored["calendar"]["date"], stored["securities"]
    audited = calendar.filter(calendar.is_in(days))
    new_audited = bars_new.filter(pl.col("date").is_in(audited.implode()))
    bar_issues, compared = audit_bars(new_audited, old, status_new, securities, source)
    stats = {
        "bj_rows": bj_rows,
        **_bar_stats(compared, bars_new, status_new, old, securities, audited),
        "resolutions": counts,
        "detections": _detection_stats(found, cut),
        "turn": _turn_counts(bars_new),
    }
    issues = pl.concat([duplicates, resolved, bar_issues])
    return pl.concat([kept, bars_new]), bars_new, status_new, issues, unresolved, stats


def _resolve(raw, stored, entries, days, bars_new, old, found, source) -> tuple:
    """(new bars with the approved entries applied, their issues, the conflicts without one, counts)."""
    prices = with_references(raw, price_conflicts(bars_new, old), stored["calendar"]["date"], stored["adj_factor"])
    bars_new, resolved, unresolved = resolve(bars_new, old, prices, found, entries, source)
    resolved = first_before(resolved, stored["data_issues"])
    counts = _resolution_counts(resolved, unresolved, idle_entries(entries, resolved, days))
    return bars_new, resolved, unresolved, counts


def _stored_bars(store: Store, manifest: dict, years: set[int], days: list[dt.date]) -> tuple:
    """(SH/SZ rows on merged days, every other row, BJ rows per year before and after) of the touched years."""
    stored = read_years(store, manifest, "bars", years)
    replaced = ~BJ & pl.col("date").is_in(days)
    old, kept = stored.filter(replaced), stored.filter(~replaced)
    before = _counts(stored.filter(BJ), pl.col("date").dt.year())
    after = _counts(kept.filter(BJ), pl.col("date").dt.year())
    return old, kept, {y: [before.get(y, 0), after.get(y, 0)] for y in sorted(before.keys() | after.keys())}


def _merge_status(store, manifest, status_new, spans, years, days, source) -> tuple:
    """(new status rows, daily_status parts, before_first_bar issues): no row, stored or new, before its code's first
    bar."""
    status_new, early_new = drop_status_before_first_bar(status_new, spans, source)
    stored = read_years(store, manifest, "daily_status", years["daily_status"]).filter(~pl.col("date").is_in(days))
    stored, early_stored = drop_status_before_first_bar(stored, spans, source)
    return status_new, pl.concat([stored, status_new]), pl.concat([early_new, early_stored])


def _event_issues(new_adj, stored_adj, factor_codes, after, source) -> tuple[pl.DataFrame, dict]:
    """(issues, counts) of the factor files' events against the store's, for the codes with a factor file."""
    bar_dates = after.select(KEY)
    new = events(new_adj, bar_dates)
    old = events(stored_adj.filter(pl.col("code").is_in(factor_codes)), bar_dates)
    return audit_events(new, old, source), _event_counts(new, old)


def _checked_parts(manifest: dict, parts: dict, years: dict) -> dict:
    """parts, once every touched year with a stored file keeps a row: Store.commit keeps a part its frame leaves out."""
    for table, frame in parts.items():
        emptied = (set(part_rows(manifest, table)) & years[table]) - set(frame["date"].dt.year())
        if emptied:
            raise ValueError(f"the merge leaves no {table} rows in {sorted(emptied)}; a stored year cannot be dropped")
    return parts


def _whole_tables(securities, data_issues, adj, stored_adj) -> dict[str, pl.DataFrame]:
    """securities and data_issues, plus adj_factor when its rows change."""
    tables = {"securities": securities, "data_issues": data_issues}
    return tables if _same_rows(adj, stored_adj, ["code", "ex_date"]) else tables | {"adj_factor": adj}


def _rows(manifest: dict, parts: dict, years: dict, tables: dict) -> dict[str, int]:
    """Rows of each table after the merge; a partitioned table adds its untouched stored parts."""
    untouched = {t: sum(n for y, n in part_rows(manifest, t).items() if y not in years[t]) for t in parts}
    return {t: len(frame) + untouched.get(t, 0) for t, frame in (parts | tables).items()}


def _conflict_issues(conflicts: pl.DataFrame, source: str) -> pl.DataFrame:
    rows = conflicts.filter(pl.col("tradestatus").is_not_null())
    return pl.concat(
        issue_rows(
            rows.filter(pl.col("action") == action),
            "daily_status",
            "tradestatus_conflict",
            action,
            ["tradestatus", "volume"],
            source,
        )
        for action in ("bar_kept", "no_bar")
    )


def _same_rows(a: pl.DataFrame, b: pl.DataFrame, key: list[str]) -> bool:
    return a.sort(key).equals(b.sort(key))


def _meta(store: Store, manifest: dict, status_parts: pl.DataFrame, years: set[int], calendar: pl.Series) -> dict:
    """last_trade_date and the daily_status coverage after the merge."""
    untouched = store.scan("daily_status", columns=["date"], manifest=manifest)
    untouched = untouched.filter(~pl.col("date").dt.year().is_in(sorted(years)))
    dates = pl.concat([status_parts.lazy().select("date"), untouched]).unique().collect(engine="streaming")["date"]
    coverage = {
        "first_day": _iso(dates.min()),
        "last_day": _iso(dates.max()),
        "days": dates.len(),
    }
    return {"last_trade_date": _iso(calendar.max()), "baostock_merge": coverage}


def _iso(day: dt.date | None) -> str | None:
    return day.isoformat() if day is not None else None


def _counts(frame: pl.DataFrame, by: str | pl.Expr) -> dict:
    """{value: rows} of frame grouped by one column or expression."""
    return dict(frame.group_by(by).len().sort(pl.first()).rows())


def _layout_only(manifest: dict, years: set[int]) -> dict:
    """Stale bars years without a merged day, rewritten only to gain the current columns."""
    rows = part_rows(manifest, "bars")
    return {"years": sorted(years), "rows": sum(rows[y] for y in years)}


def _resolution_counts(resolved: pl.DataFrame, unresolved: pl.DataFrame, idle: pl.DataFrame) -> dict:
    """Applied entries by kind and action, conflicts without an approved entry by state, and the approved entries that
    met no conflict."""
    return {
        "applied": _counts(resolved, pl.concat_str("kind", "action", separator=" ")),
        "unresolved": state_counts(unresolved),
        "idle": idle.to_dicts(),
    }


def _detection_stats(found: pl.DataFrame, truncated: pl.DataFrame) -> dict:
    """Bars each detection marked, and the exchange-days whose snapshot looks cut against the seed."""
    days = truncated.with_columns(pl.col("share").round(4)).to_dicts()
    return {mark: int(found[mark].sum()) for mark in MARKS} | {"truncated_days": days}


def _turn_counts(bars: pl.DataFrame) -> dict:
    """Merged BaoStock bars, those with a turn, and those trading without one."""
    missing = pl.col("turn").is_null() & (pl.col("volume") > 0)
    return {
        "bars": bars.height,
        "non_null": bars["turn"].is_not_null().sum(),
        "null_traded": bars.filter(missing).height,
    }


def _merged_counts(bars: pl.DataFrame, status: pl.DataFrame) -> dict:
    return {
        "bars": bars.height,
        "daily_status": status.height,
        "suspended": status.filter(pl.col("tradestatus") == 0).height,
        "st": status.filter(pl.col("is_st")).height,
    }


def _bar_stats(compared, bars_new, status_new, old, securities, audited) -> dict:
    """Compared rows, replaced exchange-preclose counts, relative errors and seed gaps on audited days."""
    derived = compared.filter(pl.col("store_preclose_derived"))
    spans = securities.select("code", "first_date", "last_date")
    listed = status_new.filter(pl.col("date").is_in(audited.implode())).join(spans, on="code")
    gaps = listed.filter(pl.col("date").is_between("first_date", "last_date")).join(old, on=KEY, how="anti")
    return {
        "compared": compared.height,
        "preclose": {
            "replaced": derived.height,
            "differing": derived.filter(pl.col("preclose") != pl.col("store_preclose")).height,
        },
        "volume_rel": _quantiles(compared["volume_rel"]),
        "amount_rel": _quantiles(compared["amount_rel"]),
        "seed_gaps": {
            "tradestatus_0": gaps.filter(pl.col("tradestatus") == 0).height,
            "traded": gaps.join(bars_new, on=KEY, how="semi").height,
        },
    }


def _adj_changes(old: pl.DataFrame, new: pl.DataFrame, rewritten: bool) -> dict:
    """Whether adj_factor is rewritten, the codes losing every row and the codes whose first row moves."""
    firsts = [frame.group_by("code").agg(pl.col("ex_date").min()) for frame in (old, new)]
    joined = firsts[0].join(firsts[1], on="code", how="left", suffix="_new").sort("code")
    return {
        "rewritten": rewritten,
        "removed": joined.filter(pl.col("ex_date_new").is_null())["code"].to_list(),
        "moved": joined.filter(pl.col("ex_date_new") != pl.col("ex_date"))["code"].to_list(),
    }


def _quantiles(values: pl.Series) -> dict:
    values = values.drop_nulls()
    if values.is_empty():
        return {"p50": None, "p99": None, "max": None}
    return {"p50": values.quantile(0.5), "p99": values.quantile(0.99), "max": values.max()}


def _event_counts(new: pl.DataFrame, old: pl.DataFrame) -> dict:
    return {
        "new": new.filter(pl.col("day").is_not_null()).height,
        "store": old.filter(pl.col("day").is_not_null()).height,
        "after_last_bar": new.filter(pl.col("day").is_null()).height,
    }


def _adjustment_stats(after: pl.DataFrame, checked: pl.DataFrame, flagged: pl.DataFrame, calendar: pl.Series) -> dict:
    exchange = after.filter(~pl.col("preclose_derived")).height
    return {
        "checked": checked.height,
        "first_bar": exchange - checked.height,
        "agree": checked.filter(pl.col("agrees")).height,
        "flagged": _counts(flagged, "kind"),
        "late_seed_events": _late_seed_events(checked, flagged, calendar),
    }


def _late_seed_events(checked: pl.DataFrame, flagged: pl.DataFrame, calendar: pl.Series) -> list[dict]:
    """event_missing rows followed within LATE_EVENT_DAYS trading days by an event_unimplied of the same step."""
    index = pl.DataFrame({"date": calendar, "i": pl.int_range(calendar.len(), eager=True)})
    rows = flagged.select("code", "date", "kind").join(checked, on=KEY).join(index, on="date")
    missing = rows.filter(pl.col("kind") == FLAGGED[0]).select("code", "i", missing=pl.col("date"), step="implied_step")
    unimplied = rows.filter(pl.col("kind") == FLAGGED[1]).select("code", "factor_step", j="i", unimplied=pl.col("date"))
    pairs = missing.join(unimplied, on="code").with_columns(lag=pl.col("j") - pl.col("i"))
    pairs = pairs.filter(
        pl.col("lag").is_between(1, LATE_EVENT_DAYS) & ((pl.col("factor_step") / pl.col("step") - 1).abs() <= STEP_RTOL)
    )
    return (
        pairs.select("code", "missing", "unimplied", "lag", pl.col("step").round(6)).sort("code", "missing").to_dicts()
    )
