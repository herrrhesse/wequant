"""plan_merge decides every write of one merge from the store and the raw layer; apply_merge commits it as one version."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

import polars as pl

from wequant.data import codes as codemod
from wequant.data.issues import issue_rows
from wequant.data.merge.assemble import (
    BJ,
    adj_factor_table,
    bars_after,
    data_issues_table,
    read_years,
    securities_table,
    stale_years,
)
from wequant.data.merge.audit import (
    FLAGGED,
    STEP_RTOL,
    audit_bars,
    audit_events,
    check_adjustment,
    status_disagreements,
)
from wequant.data.merge.daily import read_daily, split
from wequant.data.merge.factors import events, normalize, read_factors
from wequant.data.merge.reconcile import drop_duplicate_codes, drop_status_before_first_bar
from wequant.data.raw import BaoStockRaw
from wequant.data.store import CommitResult, Store
from wequant.version import code_version

KEY = ["code", "date"]
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
    stats: dict
    meta: dict  # last_trade_date, baostock_merge {first_day, last_day, days}


class StalePlan(RuntimeError):
    """The store committed a new version after the plan was made."""


def plan_merge(
    store: Store, raw: BaoStockRaw, days: Iterable[dt.date] | None = None, factor_codes: Iterable[str] | None = None
) -> MergePlan:
    """What one merge of raw snapshots (None: every day on disk) and factor files (None: every file) writes; reads only."""
    manifest = store.manifest()
    if manifest is None:
        raise ValueError(f"{store.home} has no committed version to merge into")
    calendar = store.read_table("calendar", manifest=manifest)["date"]
    days = _merge_days(raw, days, calendar)
    factor_codes = _factor_codes(raw, factor_codes)
    securities = store.read_table("securities", manifest=manifest)
    bars_new, status_new = split(read_daily(raw, days))
    source = _newest(status_new["source"])

    stale = stale_years(store, manifest, "bars")
    years = {d.year for d in days} | stale
    old, kept, bj_rows = _stored_bars(store, manifest, years, days)
    bars_new, status_new, duplicates = drop_duplicate_codes(bars_new, status_new, old, source)
    audited = calendar.filter(calendar.is_in(days))
    new_audited = bars_new.filter(pl.col("date").is_in(audited.implode()))
    bar_issues, compared = audit_bars(new_audited, old, status_new, securities, source)
    bar_stats = _bar_stats(compared, bars_new, status_new, old, securities, audited)
    parts = pl.concat([kept, bars_new])
    del old, kept, new_audited, compared  # the largest frames; the whole-store steps below need the memory

    after = bars_after(store, manifest, parts, years)
    spans = after.group_by("code").agg(first_date=pl.col("date").min(), last_date=pl.col("date").max())
    status_new, early = drop_status_before_first_bar(status_new, spans, source)
    status_parts = pl.concat([_stored_status(store, manifest, years, days), status_new])

    stored_adj = store.read_table("adj_factor", manifest=manifest)
    in_files = pl.col("code").is_in(factor_codes)
    new_adj = normalize(read_factors(raw, factor_codes), spans.filter(in_files))
    new_events, old_events = (events(a, after.select(KEY)) for a in (new_adj, stored_adj.filter(in_files)))
    event_issues = audit_events(new_events, old_events, source)

    securities_new, no_bars = securities_table(securities, spans, source)
    adj = adj_factor_table(stored_adj, new_adj, factor_codes, spans)
    flagged, checked = check_adjustment(after, adj.filter(~BJ), source)
    conflicts = _conflict_issues(status_disagreements(bars_new, status_new), source)

    record = pl.concat([duplicates, early, bar_issues, event_issues, no_bars, conflicts])
    issues = pl.concat([record, flagged])
    data_issues = data_issues_table(store.read_table("data_issues", manifest=manifest), record, flagged)
    replace = {"securities": securities_new, "data_issues": data_issues}
    if not _same_rows(adj, stored_adj, ["code", "ex_date"]):
        replace["adj_factor"] = adj
    replace_parts = {"bars": parts, "daily_status": status_parts}
    full_calendar = pl.concat([calendar, pl.Series("date", days)]).unique().sort()
    stats = {
        "rows": {k: v.height for k, v in (replace_parts | replace).items()},
        "merged": _merged_counts(bars_new, status_new),
        "years": {"touched": sorted(years), "stale": sorted(stale)},
        "bj_rows": bj_rows,
        **bar_stats,
        "events": _event_counts(new_events, old_events),
        "adj_factor": _adj_changes(stored_adj.filter(~BJ), adj.filter(~BJ), "adj_factor" in replace),
        "adjustment": _adjustment_stats(after, checked, flagged, full_calendar),
        "issues": _counts(issues, "kind"),
    }
    return MergePlan(
        base_version=manifest["version"],
        days=days,
        factor_codes=factor_codes,
        replace_parts=replace_parts,
        replace=replace,
        upsert={"calendar": pl.DataFrame({"date": days}, schema={"date": pl.Date})},
        issues=issues,
        stats=stats,
        meta=_meta(store, manifest, status_parts, years, full_calendar),
    )


def apply_merge(store: Store, plan: MergePlan, timeout: float = 0) -> CommitResult:
    """Commit the plan, code_version added to its block, as one version under the writer lock; StalePlan if the store moved on."""
    with store.lock(timeout):
        current = store.manifest()["version"]
        if current != plan.base_version:
            raise StalePlan(f"planned on version {plan.base_version}, the store is at version {current}")
        block = {**plan.meta["baostock_merge"], "code_version": code_version()}
        return store.commit(
            {**plan.meta, "baostock_merge": block},
            replace_parts=plan.replace_parts,
            replace=plan.replace,
            upsert=plan.upsert,
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


def _stored_bars(store: Store, manifest: dict, years: set[int], days: list[dt.date]) -> tuple:
    """(SH/SZ rows on merged days, every other row, BJ rows per year before and after) of the touched years."""
    stored = read_years(store, manifest, "bars", years)
    replaced = ~BJ & pl.col("date").is_in(days)
    old, kept = stored.filter(replaced), stored.filter(~replaced)
    before = _counts(stored.filter(BJ), pl.col("date").dt.year())
    after = _counts(kept.filter(BJ), pl.col("date").dt.year())
    return old, kept, {y: [before.get(y, 0), after.get(y, 0)] for y in sorted(before.keys() | after.keys())}


def _stored_status(store: Store, manifest: dict, years: set[int], days: list[dt.date]) -> pl.DataFrame:
    return read_years(store, manifest, "daily_status", years).filter(~pl.col("date").is_in(days))


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
