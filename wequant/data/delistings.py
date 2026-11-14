"""Which codes left the market, from which trading day, and why: the rule that classifies a delisting, and the user's
overrides that correct it.

A code whose official listing date (securities LIST_DATE) is known has official dates: its DELIST_DATE is the official
delisting date, null while it is listed. A code without one approximates the day from last_date, the last day of the
source's data. The name at delisting is securities.name, the source's latest name; a null name is unknown.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import NamedTuple

import numpy as np
import polars as pl

from wequant.data.codes import split

KINDS = ("forced", "merger", "suspension", "unknown")
OVERRIDE_KINDS = KINDS[:3]
SOURCES = ("rule", "override")
OVERRIDES = Path("overrides") / "delisting.toml"  # under the data root, kept by the user with evidence
DELIST_DATE = "delist_date"  # the securities column of the official delisting date
LIST_DATE = "list_date"  # the securities column of the official listing date: known means official dates
FORCED_MARK = "退"  # in a name at delisting: a forced delisting
FACE_VALUE = 1.0  # yuan: a last close below it ends in a forced delisting (面值退市)
DELISTINGS = {
    "code": pl.String,
    "delist_date": pl.Date,  # official; null without one
    "day": pl.Date,  # the trading day at whose open a holding is handled; null until the calendar reaches it
    "approximate": pl.Boolean,  # day is the first trading day after last_date, not the official date's
    "last_bar": pl.Date,  # null for a code that never traded
    "last_close": pl.Float64,  # yuan, unadjusted, of the last bar
    "name": pl.String,
    "kind": pl.String,  # one of KINDS
    "classified_by": pl.String,  # one of SOURCES
    "evidence": pl.String,  # of the override; null for the rule
    "source": pl.String,  # where the override's evidence comes from; null for the rule
}
_NEVER = np.datetime64("9999-12-31", "D")
_OFFICIAL = pl.col(LIST_DATE).is_not_null()


class Override(NamedTuple):
    """The kind the user gives a code, the evidence for it and where that comes from."""

    kind: str
    evidence: str
    source: str


def overrides_text(home: Path) -> str | None:
    """The overrides file under home as text, None without one."""
    path = Path(home) / OVERRIDES
    return path.read_text("utf-8") if path.is_file() else None


def overrides_digest(text: str | None) -> str:
    """ "" without overrides, else -o and the first 12 hex of their sha256: a suffix for the identity of what uses them."""
    return "" if text is None else f"-o{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def parse_overrides(text: str | None) -> dict[str, Override]:
    """Overrides by code from the file's text: one ["<code>"] table of kind, evidence and source each; ValueError names
    the entry at fault."""
    try:
        entries = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{OVERRIDES.as_posix()}: {error}") from None
    return {code: _override(code, entry) for code, entry in entries.items()}


def delisted(securities: pl.DataFrame, calendar: np.ndarray) -> np.ndarray:
    """Whether each code left the market or looks as if it did: it has an official delisting date, or its data ends
    before the calendar does."""
    ended = securities["last_date"].to_numpy() < calendar[-1]
    return ended | securities[DELIST_DATE].is_not_null().to_numpy()


def classify(codes: pl.DataFrame, calendar: np.ndarray, overrides: dict[str, Override]) -> pl.DataFrame:
    """DELISTINGS of delisted codes from their securities columns, last_bar and last_close, in their order.

    The kind is an override's, else suspension for a code its official dates leave listed, else forced for a name
    with FORCED_MARK or a last close below FACE_VALUE, else merger for a named official delisting, else unknown. An
    override sets the kind, never the day; one for a code that is not delisted is not used.
    """
    codes = codes.select("code", DELIST_DATE, "last_date", "last_bar", "last_close", "name", official=_OFFICIAL)
    given = pl.DataFrame(
        [(code, *entry) for code, entry in overrides.items()],
        schema={"code": pl.String, "override": pl.String, "evidence": pl.String, "source": pl.String},
        orient="row",
    )
    days = pl.Series(_days(codes, calendar), dtype=pl.Date)
    found = codes.with_columns(day=days, approximate=~pl.col("official"))
    found = found.join(given, on="code", how="left", maintain_order="left")
    by_rule = _rule()
    return found.select(
        *list(DELISTINGS)[:7],
        kind=pl.coalesce("override", by_rule),
        classified_by=pl.when(pl.col("override").is_null()).then(pl.lit("rule")).otherwise(pl.lit("override")),
        evidence="evidence",
        source="source",
    ).cast(DELISTINGS)


def _rule() -> pl.Expr:
    """The kind the rule gives a delisted code."""
    name, date = pl.col("name"), pl.col(DELIST_DATE)
    return (
        pl.when(pl.col("official") & date.is_null())
        .then(pl.lit("suspension"))
        .when(name.str.contains(FORCED_MARK, literal=True) | (pl.col("last_close") < FACE_VALUE))
        .then(pl.lit("forced"))
        .when(date.is_not_null() & name.is_not_null())
        .then(pl.lit("merger"))
        .otherwise(pl.lit("unknown"))
    )


def _days(codes: pl.DataFrame, calendar: np.ndarray) -> np.ndarray:
    """Per code, the first trading day on or after the official date and after the last bar, or without official
    dates the first trading day after last_date; NaT until the calendar has it."""
    on_or_after = np.searchsorted(calendar, _dates(codes[DELIST_DATE], _NEVER), "left")
    before = calendar[0] - np.timedelta64(1, "D")
    official = np.maximum(on_or_after, np.searchsorted(calendar, _dates(codes["last_bar"], before), "right"))
    approximate = np.searchsorted(calendar, _dates(codes["last_date"], _NEVER), "right")
    row = np.where(codes["official"].to_numpy(), official, approximate)
    return np.where(row < len(calendar), calendar[np.minimum(row, len(calendar) - 1)], np.datetime64("NaT", "D"))


def _dates(column: pl.Series, missing: np.datetime64) -> np.ndarray:
    """column's dates as datetime64[D], missing where null."""
    days = column.to_numpy().astype("datetime64[D]")
    return np.where(np.isnat(days), missing, days)


def _override(code: str, entry: object) -> Override:
    """The override of one entry; ValueError naming it unless it is a code with exactly a known kind, evidence and
    source, each non-empty text."""
    where = f"{OVERRIDES.as_posix()}: [{code!r}]"
    try:
        split(code)
    except ValueError:
        raise ValueError(f"{where} is not a code such as 600000.SH") from None
    if not isinstance(entry, dict) or set(entry) != set(Override._fields):
        raise ValueError(f"{where} needs exactly {', '.join(Override._fields)}")
    if entry["kind"] not in OVERRIDE_KINDS:
        raise ValueError(f"{where}: kind {entry['kind']!r} is not one of {OVERRIDE_KINDS}")
    if not all(isinstance(entry[key], str) and entry[key].strip() for key in Override._fields):
        raise ValueError(f"{where}: kind, evidence and source are non-empty text")
    return Override(**entry)
