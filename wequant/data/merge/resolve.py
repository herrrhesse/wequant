"""Approved resolutions applied to the new bars, each recorded as one data_issues row."""

from __future__ import annotations

import datetime as dt
import json

import polars as pl

from wequant.data.issues import encode
from wequant.data.merge.conflicts import KEY, REFERENCE, SEEDED, average_conflicts
from wequant.data.merge.daily import paid
from wequant.data.merge.resolutions import ENTRY_KEY
from wequant.data.schema import DATA_ISSUES

LISTED = 20  # conflicts an UnresolvedConflicts message names at most
STATES = {  # why a conflict lacks an approved entry, in the order a stop lists them
    "missing": "no entry (run propose)",
    "unfetched": "no entry yet: propose waits for the next trading day's snapshot, its reference",
    "proposed": "a proposed entry awaits approval",
    "unpriced": "an amount conflict on BaoStock's prices; it waits for the approval of its cell's price entry",
}
ENTRY = pl.Enum(list(STATES))


class UnresolvedConflicts(RuntimeError):
    """Conflicts without an approved resolution; nothing is written."""

    def __init__(self, rows: pl.DataFrame):
        counts = state_counts(rows)
        states = ", ".join(f"{n} {state}" for state, n in counts.items())
        lines = [f"{code} {day} {kind}: {STATES[e]}" for code, day, kind, e in rows.select(*ENTRY_KEY, "entry").rows()]
        more = [f"... and {rows.height - LISTED} more"] if rows.height > LISTED else []
        head = f"{rows.height} conflicts lack an approved resolution ({states}):"
        super().__init__("\n".join([head, *lines[:LISTED], *more]))
        self.rows = rows


def resolve(
    bars: pl.DataFrame, old: pl.DataFrame, prices: pl.DataFrame, entries: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(bars with the approved entries applied, their data_issues rows, the conflicts without one): prices holds the
    price conflicts with their references; amounts are checked on the bars as the approved price entries leave them."""
    covered, price_waiting = settle(prices, entries)
    bars, price_issues = apply_resolutions(bars, covered, source)
    averages = average_conflicts(bars, old).join(prices.select(*KEY, *REFERENCE), on=KEY, how="left")
    unpriced = averages.join(price_waiting, on=KEY, how="semi").with_columns(entry=pl.lit("unpriced", dtype=ENTRY))
    covered, amount_waiting = settle(averages.join(price_waiting, on=KEY, how="anti"), entries)
    bars, amount_issues = apply_resolutions(bars, covered, source)
    unresolved = pl.concat([price_waiting, amount_waiting, unpriced]).sort("entry", "date", "code", "kind")
    return bars, pl.concat([price_issues, amount_issues]), unresolved


def settle(conflicts: pl.DataFrame, entries: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(conflicts with their approved entry's fields, take and evidence; the others, with entry: a STATES key)."""
    approved = entries.filter(pl.col("status") == "approved").select(*ENTRY_KEY, "fields", "take", "evidence")
    proposed = entries.filter(pl.col("status") == "proposed").select(*ENTRY_KEY, proposed=pl.lit(True))
    rest = conflicts.join(approved, on=ENTRY_KEY, how="anti").join(proposed, on=ENTRY_KEY, how="left")
    unfetched = (pl.col("kind") == "price_mismatch") & ~pl.col("fetched")
    entry = (
        pl.when(pl.col("proposed"))
        .then(pl.lit("proposed"))
        .when(unfetched)
        .then(pl.lit("unfetched"))
        .otherwise(pl.lit("missing"))
    )
    rest = rest.with_columns(entry=entry.cast(ENTRY)).drop("proposed")
    return conflicts.join(approved, on=ENTRY_KEY).sort(ENTRY_KEY), rest


def apply_resolutions(bars: pl.DataFrame, covered: pl.DataFrame, source: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(bars with each covered entry's take applied, data_issues rows): one per entry, plus close_unconfirmed for a
    price_mismatch whose kept close is not the next day's exchange preclose."""
    rows = covered.to_dicts()
    issues = pl.DataFrame([issue for row in rows for issue in _issues(row, source)], schema=DATA_ISSUES.schema)
    if not rows:
        return bars, issues
    updates = [{c: row[c] for c in (*KEY, *SEEDED)} | _after(row) for row in rows]
    updates = pl.DataFrame(updates, schema=bars.select(*KEY, *SEEDED).schema)
    _check_paid(updates.join(covered.filter(pl.col("fields").list.contains("amount")), on=KEY, how="semi"))
    return bars.update(updates, on=KEY, include_nulls=True), issues


def first_before(issues: pl.DataFrame, old: pl.DataFrame) -> pl.DataFrame:
    """issues, each resolved row's before taken from an earlier record of its key in old when there is one: the store's
    values ahead of the cell's first resolution, which the store no longer holds."""
    key = list(DATA_ISSUES.key)
    earlier = old.filter(pl.col("action").str.starts_with("resolved_")).join(issues, on=key, how="semi")
    firsts = {tuple(row[:-1]): json.loads(row[-1])["before"] for row in earlier.select(*key, "detail").rows()}
    if not firsts:
        return issues
    rows = issues.select(*key, "detail").rows()
    details = [_carried(detail, firsts[tuple(k)]) if tuple(k) in firsts else detail for *k, detail in rows]
    return issues.with_columns(detail=pl.Series(details, dtype=pl.String))


def idle_entries(entries: pl.DataFrame, issues: pl.DataFrame, days: list[dt.date]) -> pl.DataFrame:
    """Approved entries on these days taking other than BaoStock's values that met no conflict: the bars keep
    BaoStock's values there, not the entry's."""
    applied = issues.filter(pl.col("action").str.starts_with("resolved_"))
    wanted = (pl.col("status") == "approved") & (pl.col("take") != "baostock") & pl.col("date").is_in(days)
    idle = entries.filter(wanted).join(applied, on=ENTRY_KEY, how="anti")
    return idle.select(*ENTRY_KEY, "fields", "take").sort("date", "code", "kind")


def state_counts(unresolved: pl.DataFrame) -> dict[str, int]:
    """Conflicts without an approved entry, per STATES key."""
    counts = dict(unresolved.group_by(pl.col("entry").cast(pl.String)).len().rows())
    return {state: counts.get(state, 0) for state in STATES}


def _issues(row: dict, source: str) -> list[dict]:
    """The entry's data_issues row, plus close_unconfirmed when the next day's exchange preclose does not repeat the
    kept close."""
    fields, after = row["fields"], _after(row)
    before = None if row["seed_close"] is None else {f: row[f"seed_{f}"] for f in fields}  # None: no store bar
    baostock = {f: row[f] for f in fields}
    detail = {"fields": fields, "before": before, "baostock": baostock, "after": after, "evidence": row["evidence"]}
    issues = [_issue(row, row["kind"], f"resolved_{row['take']}", detail, source)]
    if row["kind"] == "price_mismatch" and not _confirmed(row, after["close"]):
        issues.append(_issue(row, "close_unconfirmed", "flagged", _reference(row, after["close"]), source))
    return issues


def _carried(detail: str, first: dict | None) -> str:
    """detail with before replaced by an earlier record's, its fields completed from the current before."""
    values = json.loads(detail)
    current = values["before"] or {}
    values["before"] = None if first is None else {f: first.get(f, current.get(f)) for f in values["fields"]}
    return encode(values)


def _check_paid(bars: pl.DataFrame) -> None:
    """ValueError when an entry deciding the amount leaves it outside its bar's [low, high]."""
    broken = bars.filter(pl.col("amount").is_not_null() & ~paid())
    if broken.height:
        cells = ", ".join(f"{code} {day}" for code, day in broken.select(KEY).rows())
        raise ValueError(f"approved entries leave amounts outside [low, high]; change them: {cells}")


def _after(row: dict) -> dict:
    """The values of the entry's fields once its take is applied."""
    if row["take"] == "null":
        return {"amount": None}
    if row["take"] == "baostock":
        return {f: row[f] for f in row["fields"]}
    missing = [f for f in row["fields"] if row[f"seed_{f}"] is None]
    if missing:
        raise ValueError(
            f"the resolution of {row['code']} {row['date']} takes the seed's {missing}; the store has none"
        )
    return {f: row[f"seed_{f}"] for f in row["fields"]}


def _confirmed(row: dict, close: int) -> bool:
    return not row["ex_event"] and row["next_preclose"] is not None and close == row["next_preclose"]


def _reference(row: dict, close: int) -> dict:
    """The kept close, both sources' closes and the next day's exchange preclose, for a close_unconfirmed row."""
    closes = {"close": close, "baostock_close": row["close"], "seed_close": row["seed_close"]}
    return closes | {name: row[name] for name in ("next_date", "next_preclose", "ex_event")}


def _issue(row: dict, kind: str, action: str, detail: dict, source: str) -> dict:
    cell = {"code": row["code"], "date": row["date"]}
    return {"table": "bars", **cell, "kind": kind, "action": action, "detail": encode(detail), "source": source}
