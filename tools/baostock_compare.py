"""Refetched BaoStock day snapshots compared with the backfill's, row by row; local files only.

    raw/baostock/refetch/<YYYY-MM-DD>.parquet   against   raw/baostock/daily/<YYYY>/<YYYY-MM-DD>.parquet

Rows match on (date, code); every column but _request and _fetched_at is compared as text. A resolutions file
([[resolution]] tables with code, date, kind and a non-empty fields list) adds one line per listed row on a
refetched day.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from tools.baostock_backfill import refetch_path, refetched_days
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import to_baostock

KEY = ["date", "code"]
UNCOMPARED = {"_request", "_fetched_at"}
ALWAYS_SHOWN = ["volume", "amount"]  # shown for every listed row besides its own fields
SAMPLES = 10


@dataclass(frozen=True)
class Pair:
    """One day's backfill (old) and refetch (new) rows joined on KEY; new values carry the suffix _new."""

    joined: pl.DataFrame
    columns: list[str]  # value columns in both files
    one_sided: list[str]  # columns in one file only
    old_rows: int
    new_rows: int

    @classmethod
    def read(cls, raw: BaoStockRaw, day: dt.date) -> Pair | None:
        """None when daily/ has no file for the day."""
        if not raw.daily(day).exists():
            return None
        old, new = pl.read_parquet(raw.daily(day)), pl.read_parquet(refetch_path(raw, day))
        columns = [c for c in old.columns if c in new.columns and c not in KEY and c not in UNCOMPARED]
        joined = (
            old.select(*KEY, *columns, _old=pl.lit(True))
            .join(new.select(*KEY, *columns, _new=pl.lit(True)), on=KEY, how="full", coalesce=True, suffix="_new")
            .with_columns(pl.col("_old", "_new").fill_null(False))
        )
        one_sided = sorted((set(old.columns) ^ set(new.columns)) - UNCOMPARED)
        return cls(joined, columns, one_sided, old.height, new.height)

    def only(self, side: str) -> list[str]:
        """Codes of the rows only in the old ("_old") or only in the new ("_new") file."""
        other = "_new" if side == "_old" else "_old"
        return self.joined.filter(pl.col(side) & ~pl.col(other))["code"].sort().to_list()

    def both(self) -> pl.DataFrame:
        return self.joined.filter(pl.col("_old") & pl.col("_new"))

    def differing(self) -> pl.DataFrame:
        return self.both().filter(pl.any_horizontal(_changed(c) for c in self.columns)).sort("code")

    def counts(self) -> dict[str, int]:
        """Differing rows per column, columns without a difference left out."""
        counts = self.both().select(_changed(c).sum().alias(c) for c in self.columns).row(0, named=True)
        return {c: n for c, n in counts.items() if n}


def compare(home: Path | None = None, rows: Path | None = None) -> str:
    """The Markdown comparison of every refetched day, plus the rows listed in the resolutions file `rows`."""
    raw = BaoStockRaw.at(home)
    pairs = {day: Pair.read(raw, day) for day in refetched_days(raw)}
    lines = [
        "# BaoStock refetch comparison",
        "",
        f"{len(pairs)} refetched days in {raw.root / 'refetch'}, each matched with its daily/ file on (date, code) "
        "over every column but _request and _fetched_at.",
        "",
        "| date | daily rows | refetch rows | only in daily | only in refetch | identical | differing |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *(_summary(day, pair) for day, pair in pairs.items()),
    ]
    for day, pair in pairs.items():
        lines += _details(day, pair)
    if rows is not None:
        lines += _listed(rows, pairs)
    return "\n".join(lines) + "\n"


def _summary(day: dt.date, pair: Pair | None) -> str:
    if pair is None:
        return f"| {day} | no file | | | | | |"
    differing = pair.differing().height
    only_old, only_new, identical = len(pair.only("_old")), len(pair.only("_new")), pair.both().height - differing
    return f"| {day} | {pair.old_rows:,} | {pair.new_rows:,} | {only_old:,} | {only_new:,} | {identical:,} | {differing:,} |"


def _details(day: dt.date, pair: Pair | None) -> list[str]:
    """A section for a day with any difference: counts per column, one-sided rows and sample values."""
    if pair is None:
        return []
    differing, only_old, only_new = pair.differing(), pair.only("_old"), pair.only("_new")
    if not (differing.height or only_old or only_new or pair.one_sided):
        return []
    counts = ", ".join(f"{c} {n:,}" for c, n in pair.counts().items())
    lines = ["", f"## {day}", "", f"- Differing rows: {differing.height:,}" + (f" ({counts})" if counts else "")]
    lines += [
        f"- Only in {name}: {len(codes):,} ({_sample(codes)})"
        for name, codes in (("daily", only_old), ("refetch", only_new))
        if codes
    ]
    lines += [f"- Columns in one file only: {', '.join(pair.one_sided)}"] if pair.one_sided else []
    if differing.height:
        lines += ["", "| code | column | daily | refetch |", "| --- | --- | --- | --- |"]
        for row in differing.head(SAMPLES).iter_rows(named=True):
            lines += [
                f"| {row['code']} | {c} | {row[c]} | {row[f'{c}_new']} |"
                for c in pair.columns
                if row[c] != row[f"{c}_new"]
            ]
    return lines


def _listed(path: Path, pairs: dict[dt.date, Pair | None]) -> list[str]:
    """One line per listed (code, date, kind) on a refetched day: old and new values, and whether it reproduces."""
    entries = resolutions(path)
    shown = {key: fields for key, fields in entries.items() if pairs.get(key[1]) is not None}
    lines = [
        "",
        f"## Listed rows ({path.name})",
        "",
        f"{len(shown)} of {len(entries)} listed (code, date, kind) fall on a refetched day. Each shows its fields plus "
        "volume and amount; it reproduces when every value is the same in the refetch. Changed values are bold.",
        "",
        "| code | date | kind | daily | refetch | reproduces |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (code, day, kind), fields in sorted(shown.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        lines.append(f"| {code} | {day} | {kind} | {_listed_values(pairs[day], code, fields)} |")
    return lines


def _listed_values(pair: Pair, code: str, fields: list[str]) -> str:
    """The daily, refetch and reproduces cells of one listed row."""
    found = pair.joined.filter(pl.col("code") == to_baostock(code))
    if found.height == 0:
        return "no row | no row | in neither file"
    row = found.row(0, named=True)
    names = [c for c in dict.fromkeys([*fields, *ALWAYS_SHOWN]) if c in pair.columns]
    if not row["_old"]:
        return f"no row | {_shown(row, names, '_new')} | no daily row"
    if not row["_new"]:
        return f"{_shown(row, names, '')} | no row | no refetch row"
    changed = {c for c in names if row[c] != row[f"{c}_new"]}
    return f"{_shown(row, names, '')} | {_shown(row, names, '_new', changed)} | {'no' if changed else 'yes'}"


def _shown(row: dict, names: list[str], suffix: str, bold: set[str] = frozenset()) -> str:
    """'close 11.6000, volume 100': each name with its value in column name + suffix; names in `bold` get **...**."""
    cells = {c: f"{c} {row[c + suffix]}" for c in names}
    return ", ".join(f"**{cell}**" if c in bold else cell for c, cell in cells.items())


def resolutions(path: Path) -> dict[tuple[str, dt.date, str], list[str]]:
    """(code, date, kind) -> fields of each [[resolution]] table in a resolutions file; ValueError for a table
    without fields, since nothing would say what to compare."""
    entries: dict[tuple[str, dt.date, str], list[str]] = {}
    for table in tomllib.loads(path.read_text("utf-8")).get("resolution", []):
        code, day, kind = table["code"], dt.date.fromisoformat(str(table["date"])), table["kind"]
        if not table.get("fields"):
            raise ValueError(f"{path}: the resolution for {code} {day} {kind} lists no fields")
        entries.setdefault((code, day, kind), []).extend(table["fields"])
    return entries


def _changed(column: str) -> pl.Expr:
    return ~pl.col(column).eq_missing(pl.col(f"{column}_new"))


def _sample(codes: list[str]) -> str:
    return ", ".join(codes[:SAMPLES]) + (", ..." if len(codes) > SAMPLES else "")
