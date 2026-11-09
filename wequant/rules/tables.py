"""Reading limit inputs from the store and writing the limit check back."""

from __future__ import annotations

import contextlib
import functools
from dataclasses import dataclass

import polars as pl

from wequant.data.store import Store, tables_digest
from wequant.rules.book import RuleBook, default_book
from wequant.rules.check import KIND, limit_issues, missing_issues, void
from wequant.rules.limits import INPUT, LIMITS_FORMAT, price_limits
from wequant.rules.phase import add_gap_days, add_listing_day, list_dates
from wequant.version import code_version

LIMIT_INPUTS = ("bars", "securities", "daily_status", "adj_factor", "calendar")
BAR_COLUMNS = ["code", "date", "high", "low", "close", "preclose", "preclose_derived"]
EXTRA = ["high", "low", "close", "preclose_derived", "gap_days", "ex_date"]
LIMITS = ["code", "date", "limit_up", "limit_down", "limit_state"]


def limit_inputs(store: Store, manifest: dict | None = None) -> pl.DataFrame:
    """INPUT plus high, low, close, preclose_derived, gap_days and ex_date (Boolean) for every bar of one snapshot, sorted by (code, date)."""
    manifest = _pinned(store, manifest)
    scan = functools.partial(store.scan, manifest=manifest)
    securities = scan("securities", columns=["code", "exchange", "board", "first_date"]).collect()
    calendar = scan("calendar", columns=["date"]).collect()["date"]
    events = _events(scan("adj_factor", columns=["code", "ex_date"]), scan("bars", columns=["code", "date"]))
    frame = (
        scan("bars", columns=BAR_COLUMNS)
        .join(securities.lazy().select("code", "board"), on="code", how="left")
        .join(list_dates(securities).lazy(), on="code", how="left")
        .join(scan("daily_status", columns=["code", "date", "is_st"]), on=["code", "date"], how="left")
        .join(events.lazy(), on=["code", "date"], how="left")
        .with_columns(pl.col("ex_date").fill_null(False))
        .sort("code", "date")
        .collect()
    )
    frame = add_gap_days(add_listing_day(frame, calendar), calendar)
    return frame.select(*INPUT, *EXTRA)


def load_limits(store: Store, manifest: dict | None = None, book: RuleBook | None = None) -> pl.DataFrame:
    """code, date, limit_up, limit_down (Int32 厘, null unless LIMITED), limit_state (Int8) for every bar of one snapshot, voids applied, sorted by (code, date)."""
    manifest = _pinned(store, manifest)
    frame = price_limits(limit_inputs(store, manifest), book)
    recorded = store.read_table("data_issues", manifest=manifest).filter(pl.col("kind") == KIND)
    return void(frame, recorded).select(LIMITS)


def limit_check_current(manifest: dict, book: RuleBook | None = None) -> bool:
    """True when the manifest's limit_check block matches its rules digest, LIMITS_FORMAT and the inputs' tables_digest."""
    block = manifest.get("limit_check") or {}
    return all(block.get(key) == value for key, value in _identity(manifest, book or default_book()).items())


@dataclass
class CheckResult:
    version: int  # store version checked
    digest: str  # rule book digest
    frame: pl.DataFrame  # limit_inputs + price_limits, for the report
    issues: pl.DataFrame  # wanted limit_breach rows
    missing: pl.DataFrame  # wanted, not recorded at the checked version
    stale: pl.DataFrame  # recorded, no longer wanted
    committed: int | None  # new store version if a commit happened


def check_store(store: Store, commit: bool = False, book: RuleBook | None = None) -> CheckResult:
    """Check every bar of the current version; with commit, replace its limit_breach rows and its limit_check block under the writer lock."""
    book = book or default_book()
    with store.lock() if commit else contextlib.nullcontext():
        manifest = _pinned(store, None)
        frame = price_limits(limit_inputs(store, manifest), book)
        issues = limit_issues(frame, f"limit_check@{book.digest[:12]}")
        data_issues = store.read_table("data_issues", manifest=manifest)
        recorded = data_issues.filter(pl.col("kind") == KIND)
        missing, stale = missing_issues(issues, recorded), missing_issues(recorded, issues)
        result = CheckResult(manifest["version"], book.digest, frame, issues, missing, stale, None)
        if commit:
            table = pl.concat([data_issues.filter(pl.col("kind") != KIND), issues])
            done = store.commit({"limit_check": _block(manifest, book)}, replace={"data_issues": table})
            result.committed = done.version if done.changed else None
    return result


def _block(manifest: dict, book: RuleBook) -> dict:
    return {"code_version": code_version(), **_identity(manifest, book)}


def _identity(manifest: dict, book: RuleBook) -> dict:
    """What a recorded check must match to be current: the rule book, the limit format and the inputs' content."""
    return {"rules": book.digest, "format": LIMITS_FORMAT, "inputs": tables_digest(manifest, LIMIT_INPUTS)}


def _pinned(store: Store, manifest: dict | None) -> dict:
    manifest = manifest if manifest is not None else store.manifest()
    if manifest is None:
        raise LookupError(f"nothing committed in {store.home}")
    return manifest


def _events(adj: pl.LazyFrame, days: pl.LazyFrame) -> pl.DataFrame:
    """(code, date, ex_date True) of the first bar on or after each adjustment event after the code's first adj_factor row."""
    events = adj.filter(pl.col("ex_date") > pl.col("ex_date").min().over("code")).sort("ex_date")
    first_bars = events.join_asof(
        days.sort("code", "date"),
        left_on="ex_date",
        right_on="date",
        by="code",
        strategy="forward",
        check_sortedness=False,
    )
    # Collected here, so the sorted bar days are freed before limit_inputs builds the full bar frame.
    return first_bars.select("code", "date", ex_date=pl.lit(True)).drop_nulls("date").unique().collect()
