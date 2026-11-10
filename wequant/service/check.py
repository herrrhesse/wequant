"""data check: every bar of the current version against its price limits; breaches void that day's limits."""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from wequant.data.files import atomic_write
from wequant.data.store import Store, StoreLocked
from wequant.rules.book import RuleBook, default_book
from wequant.rules.check import KIND, missing_issues
from wequant.rules.tables import CheckResult, check_store, limit_check_current
from wequant.service.base import Check, Context, Model, manifest
from wequant.service.errors import Exit, ServiceError
from wequant.service.limit_report import report

log = logging.getLogger(__name__)


class CheckRequest(Model):
    """The price-limit check of the current store version."""

    commit: bool = False
    """Record breaches and the limit_check block in a new version under the writer lock, and write the report."""


class CheckReport(Check):
    """The check's counts and report; ok when a current check is recorded and, in a dry run, every breach is too."""

    data_version: int
    """Version checked."""
    committed: int | None
    """New version, or null if none was made."""
    current: bool
    """The final version (committed, else checked) records a current limit check."""
    rules: str
    """Rule book digest."""
    bars: int
    """Bars checked."""
    breaches: int
    """Bars that trade outside their limits."""
    missing: int
    """Breaches found but not recorded at data_version."""
    stale: int
    """Breaches recorded at data_version but no longer found."""
    report_path: str | None
    """The report's path relative to the data root, written on commit."""
    report: str
    """The markdown report, as written on commit."""


def data_check(request: CheckRequest, context: Context = Context()) -> CheckReport:
    """Check every bar of the current version; read-only unless commit."""
    store, book = context.store(), default_book()
    manifest(store)  # NOT_FOUND without a store
    try:
        result = check_store(store, commit=request.commit, book=book)
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error)) from None
    version = result.committed or result.version
    if request.commit:
        verify(store, result, version, book)
    text = "\n".join(report(store, result, book, version, request.commit)) + "\n"
    counts = result.frame.height, result.issues.height, result.missing.height, result.stale.height
    log.info("checked store v%d: %d bars, %d breaches, %d missing, %d stale", result.version, *counts)
    path = _write(store, version, text) if request.commit else None
    current = limit_check_current(store.manifest(version), book)
    return CheckReport(
        ok=current and (request.commit or result.missing.height == result.stale.height == 0),
        data_version=result.version,
        committed=result.committed,
        current=current,
        rules=result.digest,
        bars=result.frame.height,
        breaches=result.issues.height,
        missing=result.missing.height,
        stale=result.stale.height,
        report_path=path,
        report=text,
    )


def verify(store: Store, result: CheckResult, version: int, book: RuleBook) -> None:
    """Fail unless the committed version records exactly the breaches found and a current limit_check block."""
    pinned = store.manifest(version)
    recorded = store.read_table("data_issues", manifest=pinned).filter(pl.col("kind") == KIND)
    differ = missing_issues(result.issues, recorded).height or missing_issues(recorded, result.issues).height
    if differ or not limit_check_current(pinned, book):
        raise RuntimeError(f"version {version} does not hold the limit check just committed")


def report_path(store: Store, version: int) -> Path:
    """reports/check_limits_<last trade date>_v<version>.md under the data root."""
    last = store.read_table("calendar", columns=["date"], version=version)["date"].max()
    return store.home / "reports" / f"check_limits_{last.isoformat()}_v{version}.md"


def _write(store: Store, version: int, text: str) -> str:
    """Write the report under reports/; its path relative to the data root."""
    path = report_path(store, version)
    atomic_write(path, text.encode("utf-8"))
    log.info("wrote %s", path)
    return path.relative_to(store.home).as_posix()
