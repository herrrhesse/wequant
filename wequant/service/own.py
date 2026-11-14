"""data own-check | own-import (round 3, S2.9): the user's own files in the one documented format (wequant.data.own),
checked without writing, then imported into an empty data root as one version (job kind data_own_import)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from wequant.data import own
from wequant.data.update import Step
from wequant.service.base import Check, Context, ErrorDetail, Model, guarded, name_of
from wequant.service.errors import Exit, ServiceError

Rule = Literal["missing_columns", "unreadable", "duplicate", "price_order", "no_list_date"]
STAGES = ("own_read", "own_commit")  # reading and checking the files, then writing the version


class OwnRequest(Model):
    """A folder with bars.parquet or bars.csv, and optionally securities.parquet or securities.csv."""

    path: str


class OwnProblem(Model):
    """A rule some rows break (labels.own_rules): unreadable and duplicate rows are not imported, the others are,
    flagged in data_issues."""

    rule: Rule
    rows: int
    """Rows (missing_columns: columns) that break it."""
    examples: list[list[str]]
    """The first few as [code, date] (missing_columns: [column, ""])."""


class OwnCheck(Check):
    """What the folder holds and which rows break which rule; ok False when it cannot be imported at all."""

    bars_file: str | None
    securities_file: str | None
    rows: int
    codes: int
    first: str | None
    last: str | None
    optional: list[str]
    """Optional columns found: preclose, factor, st, tradestatus."""
    problems: list[OwnProblem]


class OwnImported(Model):
    """The version made from the user's files."""

    version: int
    version_name: str
    days: int
    codes: int
    issues: int
    """data_issues rows: rows not imported and bars flagged."""


def data_own_check(request: OwnRequest, context: Context = Context()) -> OwnCheck:
    """Check the user's own files against the format and the table rules; writes nothing."""
    folder = _folder(request.path)
    found = own.check(folder)
    problems = [OwnProblem(rule=p.rule, rows=p.rows, examples=[list(e) for e in p.examples]) for p in found.problems]
    blocking = found.bars_file is None or any(p.rule == "missing_columns" for p in found.problems)
    fields = found._asdict() | {"problems": problems}
    return OwnCheck(ok=not blocking, **fields)


def data_own_import(request: OwnRequest, context: Context = Context()) -> OwnImported:
    """Import the user's own files into an empty data root as one version; a cancel before the commit leaves none."""
    folder, store = _folder(request.path), context.store()
    if store.manifest() is not None:
        message = f"{store.home} has data already; import into an empty data root"
        raise ServiceError(Exit.USAGE, message, [ErrorDetail(code="store_exists", message=message)])
    say = context.progress or (lambda step: None)
    say(Step(STAGES[0], 0.05, {}))
    try:
        tables = own.tables(folder)
    except ValueError as error:
        raise ServiceError(Exit.USAGE, str(error), [ErrorDetail(code="bad_file", message=str(error))]) from None
    if context.cancel is not None and context.cancel():
        raise ServiceError(Exit.INTERRUPTED, "import cancelled; nothing was written")
    say(Step(STAGES[1], 0.8, {}))
    days, last = tables["calendar"].height, tables["calendar"]["date"].max()
    meta = {"source": own.SOURCE, "last_trade_date": last.isoformat()}  # the version is named by it (F-29)
    with guarded():
        result = store.commit(meta, replace=tables, reason="own_import", reason_params={"days": days})
    return OwnImported(
        version=result.version,
        version_name=name_of(store, result.version),
        days=days,
        codes=tables["securities"].height,
        issues=tables["data_issues"].height,
    )


def _folder(path: str) -> Path:
    folder = Path(path).expanduser().absolute()
    if not folder.is_dir():
        message = f"{folder} is not a folder"
        raise ServiceError(Exit.NOT_FOUND, message, [ErrorDetail(code="no_directory", message=message, loc=["path"])])
    return folder
