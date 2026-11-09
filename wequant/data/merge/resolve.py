"""Approved resolutions applied to the new bars, each recorded as one data_issues row."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import encode
from wequant.data.merge.conflicts import KEY, SEEDED
from wequant.data.merge.daily import paid
from wequant.data.schema import DATA_ISSUES

ENTRY_KEY = [*KEY, "kind"]
LISTED = 20  # conflicts an UnresolvedConflicts message names at most


class UnresolvedConflicts(RuntimeError):
    """Conflicts without an approved resolution; nothing is written."""

    def __init__(self, rows: pl.DataFrame):
        waiting = int(rows["proposed"].sum())
        head = (
            f"{rows.height} conflicts lack an approved resolution "
            f"({waiting} proposed, awaiting approval; {rows.height - waiting} without an entry):"
        )
        lines = [
            f"{code} {day} {kind}: {_state(p)}" for code, day, kind, p in rows.select(*ENTRY_KEY, "proposed").rows()
        ]
        more = [f"... and {rows.height - LISTED} more"] if rows.height > LISTED else []
        super().__init__("\n".join([head, *lines[:LISTED], *more]))
        self.rows = rows


def settle(conflicts: pl.DataFrame, entries: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(conflicts with their approved entry's fields, take and evidence; the others, with proposed: whether an entry
    awaits approval)."""
    approved = entries.filter(pl.col("status") == "approved").select(*ENTRY_KEY, "fields", "take", "evidence")
    waiting = entries.filter(pl.col("status") == "proposed").select(*ENTRY_KEY, proposed=pl.lit(True))
    covered = conflicts.join(approved, on=ENTRY_KEY).sort(ENTRY_KEY)
    unresolved = conflicts.join(approved, on=ENTRY_KEY, how="anti").join(waiting, on=ENTRY_KEY, how="left")
    return covered, unresolved.with_columns(pl.col("proposed").fill_null(False)).sort(ENTRY_KEY)


def apply_resolutions(
    bars: pl.DataFrame, covered: pl.DataFrame, references: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(bars with each covered entry's take applied, data_issues rows): one per entry, plus close_unconfirmed for a
    price_mismatch whose kept close is not the next day's exchange preclose."""
    rows = covered.join(references, on=KEY, how="left").sort(ENTRY_KEY).to_dicts()
    issues = pl.DataFrame([issue for row in rows for issue in _issues(row, source)], schema=DATA_ISSUES.schema)
    cells: dict[tuple, dict] = {}
    for row in rows:
        cells.setdefault((row["code"], row["date"]), {c: row[c] for c in SEEDED}).update(_after(row))
    if not cells:
        return bars, issues
    schema = bars.select(*KEY, *SEEDED).schema
    updates = pl.DataFrame([{"code": c, "date": d, **v} for (c, d), v in cells.items()], schema=schema)
    _check_paid(updates)
    return bars.update(updates, on=KEY, include_nulls=True), issues


def _issues(row: dict, source: str) -> list[dict]:
    """The entry's data_issues row, plus close_unconfirmed when the next day's exchange preclose does not repeat the
    kept close."""
    after = _after(row)
    detail = {
        "fields": row["fields"],
        "before": {f: row[f] for f in after},
        "after": after,
        "evidence": row["evidence"],
    }
    issues = [_issue(row, row["kind"], f"resolved_{row['take']}", detail, source)]
    if row["kind"] == "price_mismatch" and not _confirmed(row, after["close"]):
        issues.append(_issue(row, "close_unconfirmed", "flagged", _reference(row, after["close"]), source))
    return issues


def _check_paid(bars: pl.DataFrame) -> None:
    """ValueError when a resolved bar keeps an amount whose average price lies outside its [low, high]."""
    broken = bars.filter(pl.col("amount").is_not_null() & ~paid())
    if broken.height:
        raise ValueError(
            f"resolutions leave amounts outside [low, high]; those cells need an amount decision:\n{broken}"
        )


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


def _state(proposed: bool) -> str:
    return "a proposed entry awaits approval" if proposed else "no entry (run propose)"
