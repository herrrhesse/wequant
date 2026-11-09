"""Store reads pinned to the plan's manifest, and the whole tables the merge replaces."""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable

import polars as pl

from wequant.data import codes as codemod
from wequant.data.issues import issue_rows
from wequant.data.merge.audit import FLAGGED
from wequant.data.merge.reconcile import BEFORE_FIRST_BAR
from wequant.data.schema import TABLES, Table
from wequant.data.store import Store

KEY = ["code", "date"]
AFTER = ["code", "date", "close", "preclose", "preclose_derived"]
BJ = pl.col("code").str.ends_with(".BJ")


def read_years(store: Store, manifest: dict, table: str, years: Iterable[int]) -> pl.DataFrame:
    """The store's rows of those years, in the table's current columns."""
    wanted = set(years)
    frames = [_conformed(store, e["path"], TABLES[table]) for e in _files(manifest, table) if int(e["part"]) in wanted]
    return pl.concat(frames).collect(engine="streaming") if frames else pl.DataFrame(schema=TABLES[table].schema)


def stale_years(store: Store, manifest: dict, table: str) -> set[int]:
    """Years whose stored file has other columns than the table's schema."""
    schema = TABLES[table].schema
    return {int(e["part"]) for e in _files(manifest, table) if pl.read_parquet_schema(store.home / e["path"]) != schema}


def part_rows(manifest: dict, table: str) -> dict[int, int]:
    """{year: rows} of the table's stored parts."""
    return {int(e["part"]): e["rows"] for e in _files(manifest, table)}


def orphan_status_years(store: Store, manifest: dict, spans: pl.DataFrame) -> set[int]:
    """Years of stored daily_status rows dated before their code's first bar in spans, or of a code not in spans."""
    rows = store.scan("daily_status", columns=KEY, manifest=manifest)
    early = rows.join(spans.lazy().select("code", "first_date"), on="code", how="left").filter(BEFORE_FIRST_BAR)
    return set(early.select(pl.col("date").dt.year().unique()).collect()["date"].to_list())


def status_to_restore(store: Store, manifest: dict, issues: pl.DataFrame, spans: pl.DataFrame) -> pl.DataFrame:
    """(code, date) of before_first_bar issues now on or after the code's first bar in spans, without a status row."""
    dropped = issues.filter(pl.col("kind") == "before_first_bar").join(spans.select("code", "first_date"), on="code")
    due = dropped.filter(pl.col("date") >= pl.col("first_date")).select(KEY)
    codes = due["code"].implode()
    stored = store.scan("daily_status", columns=KEY, manifest=manifest).filter(pl.col("code").is_in(codes))
    return due.lazy().join(stored, on=KEY, how="anti").sort(KEY).collect()


def bars_after(store: Store, manifest: dict, parts: pl.DataFrame, years: set[int]) -> pl.DataFrame:
    """code, date, close, preclose, preclose_derived of every SH/SZ bar after the merge (parts for those years, the store for the rest), sorted."""
    untouched = sorted({int(e["part"]) for e in _files(manifest, "bars")} - set(years))
    stored = [
        store.scan("bars", columns=AFTER, start=dt.date(y, 1, 1), end=dt.date(y, 12, 31), manifest=manifest)
        for y in untouched
    ]
    frame = pl.concat([parts.lazy().select(AFTER), *stored])
    return frame.filter(~BJ).sort("code", "date").collect(engine="streaming")


def securities_table(old: pl.DataFrame, spans: pl.DataFrame, source: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """SH/SZ rows with first/last_date from spans, new codes added, codes without bars removed: (table, no_bars issues)."""
    shsz = old.filter(~BJ)
    gone = shsz.join(spans, on="code", how="anti")
    kept = shsz.drop("first_date", "last_date").join(spans, on="code")
    added = spans.join(old, on="code", how="anti").with_columns(
        exchange=pl.col("code").str.slice(-2),
        board=pl.col("code").map_elements(codemod.board, return_dtype=pl.String),
        name=pl.lit(None, dtype=pl.String),
        source=pl.lit(source),
    )
    columns = list(TABLES["securities"].schema)
    table = pl.concat([old.filter(BJ), kept.select(columns), added.select(columns)]).sort("code")
    issues = issue_rows(
        gone.with_columns(date=pl.col("last_date")), "securities", "no_bars", "security_removed", ["first_date"], source
    )
    return table, issues


def adj_factor_table(old: pl.DataFrame, new: pl.DataFrame, codes: Collection[str], spans: pl.DataFrame) -> pl.DataFrame:
    """BJ rows unchanged; SH/SZ rows of codes outside codes that still have a span, first row moved back to first_date; plus new."""
    kept = old.filter(~BJ & ~pl.col("code").is_in(list(codes))).join(spans.select("code", "first_date"), on="code")
    first = pl.col("ex_date") == pl.col("ex_date").min().over("code")
    moved = (
        pl.when(first & (pl.col("first_date") < pl.col("ex_date")))
        .then(pl.col("first_date"))
        .otherwise(pl.col("ex_date"))
    )
    kept = kept.with_columns(ex_date=moved).select(list(TABLES["adj_factor"].schema))
    return pl.concat([old.filter(BJ), kept, new]).sort("code", "ex_date")


def data_issues_table(old: pl.DataFrame, record: pl.DataFrame, flagged: pl.DataFrame) -> pl.DataFrame:
    """old rows without the FLAGGED kinds, record rows replacing rows with their key, plus flagged."""
    key = list(TABLES["data_issues"].key)
    kept = old.filter(~pl.col("kind").is_in(FLAGGED)).join(record.select(key), on=key, how="anti")
    return pl.concat([kept, record, flagged]).sort(key)


def _files(manifest: dict, table: str) -> list[dict]:
    return manifest["tables"].get(table, {}).get("files", [])


def _conformed(store: Store, path: str, table: Table) -> pl.LazyFrame:
    """One stored file in the table's columns: extra columns dropped, missing ones null."""
    frame = pl.scan_parquet(store.home / path)
    present = frame.collect_schema().names()
    return frame.select(
        pl.col(c).cast(t) if c in present else pl.lit(None, dtype=t).alias(c) for c, t in table.schema.items()
    )
