"""The user's own data (round 3, S2.9): one documented format, checked before anything is written, imported into an
empty data root as one version. A row that cannot be read is dropped and a bar that breaks a price rule kept, both
recorded in data_issues; nothing is corrected.

The format, in a folder: bars.parquet or bars.csv, one row per stock and day, with code (600000.SH), date (YYYY-MM-DD),
open, high, low, close (yuan), volume (shares) and amount (yuan); optionally preclose (yuan), factor (the back
adjustment factor), st (1 or 0) and tradestatus (1 traded, 0 suspended). Optionally securities.parquet or
securities.csv with code, name, list_date and delist_date.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import polars as pl

from wequant.data.codes import board
from wequant.data.issues import issue_rows
from wequant.data.schema import TABLES

REQUIRED = ("code", "date", "open", "high", "low", "close", "volume", "amount")
OPTIONAL = ("preclose", "factor", "st", "tradestatus")
SECURITY_COLUMNS = ("code", "name", "list_date", "delist_date")
PRICES = ("open", "high", "low", "close")
CODE = r"^[0-9]{6}\.(SH|SZ|BJ)$"
SOURCE = "own"
SHOWN = 5  # rows a problem names as examples


class Problem(NamedTuple):
    """A rule some rows break: unreadable and duplicate rows are dropped, the others kept and flagged."""

    rule: str  # missing_columns, unreadable, duplicate, price_order, no_list_date
    rows: int
    examples: list[tuple[str, str]]  # (code, date) of the first few


class Found(NamedTuple):
    """What a folder holds, read only."""

    bars_file: str | None
    securities_file: str | None
    rows: int
    codes: int
    first: str | None
    last: str | None
    optional: list[str]
    problems: list[Problem]


def check(folder: Path) -> Found:
    """What the folder holds and which rows break which rule; writes nothing."""
    bars_path, securities_path = _file(folder, "bars"), _file(folder, "securities")
    if bars_path is None:
        return Found(None, _name(securities_path), 0, 0, None, None, [], [Problem("missing_columns", 0, [])])
    raw = _read(bars_path)
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        problem = Problem("missing_columns", len(missing), [(c, "") for c in missing])
        return Found(bars_path.name, _name(securities_path), raw.height, 0, None, None, [], [problem])
    bars, problems = _bars(raw)
    securities = _read(securities_path) if securities_path else None
    problems.append(_without_list_date(bars, securities))
    dates = bars["date"]
    return Found(
        bars_path.name,
        _name(securities_path),
        raw.height,
        bars["code"].n_unique(),
        dates.min() and dates.min().isoformat(),
        dates.max() and dates.max().isoformat(),
        [c for c in OPTIONAL if c in raw.columns],
        [p for p in problems if p.rows],
    )


def tables(folder: Path) -> dict[str, pl.DataFrame]:
    """The store tables of the folder's rows, data_issues included; ValueError when bars or a column is missing."""
    found = check(folder)
    if found.bars_file is None or any(p.rule == "missing_columns" for p in found.problems):
        raise ValueError(f"{folder}: no bars file with the columns {', '.join(REQUIRED)}")
    raw = _read(_file(folder, "bars"))
    bars, _ = _bars(raw)
    securities_path = _file(folder, "securities")
    given = _read(securities_path) if securities_path else None
    stored = _stored_bars(bars)
    return {
        "calendar": bars.select("date").unique().sort("date"),
        "bars": stored,
        "daily_status": _status(bars, raw.columns),
        "adj_factor": _factors(bars, "factor" in raw.columns),
        "securities": _securities(stored, given),
        "data_issues": _issues(raw, bars),
    }


def _file(folder: Path, stem: str) -> Path | None:
    return next((p for p in (folder / f"{stem}.parquet", folder / f"{stem}.csv") if p.is_file()), None)


def _name(path: Path | None) -> str | None:
    return path.name if path else None


def _read(path: Path) -> pl.DataFrame:
    """The file as strings, so that nothing is coerced before the checks."""
    if path.suffix == ".parquet":
        frame = pl.read_parquet(path)
        return frame.with_columns(pl.all().cast(pl.String))
    return pl.read_csv(path, infer_schema=False, encoding="utf8")


def _typed(raw: pl.DataFrame) -> pl.DataFrame:
    """raw with dates and numbers read, and whether each row has a code and date (keyed), all its numbers too
    (readable), and is the first readable row of its code and date (first)."""
    numbers = [*PRICES, "volume", "amount", *(c for c in ("preclose", "factor") if c in raw.columns)]
    typed = raw.with_row_index("row").with_columns(
        pl.col("date").str.strip_chars().str.to_date("%Y-%m-%d", strict=False),
        *(pl.col(c).str.strip_chars().cast(pl.Float64, strict=False).alias(c) for c in numbers),
    )
    keyed = pl.col("code").str.contains(CODE).fill_null(False) & pl.col("date").is_not_null()
    whole = pl.all_horizontal([pl.col(c).is_not_null() for c in (*PRICES, "volume", "amount")])
    typed = typed.with_columns(keyed=keyed, readable=keyed & whole)
    first = typed.filter("readable").unique(["code", "date"], keep="first", maintain_order=True)["row"]
    return typed.with_columns(first=pl.col("row").is_in(first.implode()))


def _bars(raw: pl.DataFrame) -> tuple[pl.DataFrame, list[Problem]]:
    """The readable, first-of-their-key rows, typed, and the problems of the others."""
    typed = _typed(raw)
    bars = typed.filter("first").drop("row", "keyed", "readable", "first").sort("code", "date")
    problems = [
        _problem("unreadable", typed.filter(~pl.col("readable"))),
        _problem("duplicate", typed.filter(pl.col("readable") & ~pl.col("first"))),
        _problem("price_order", bars.filter(~_fits(bars))),
    ]
    return bars, problems


def _fits(bars: pl.DataFrame) -> pl.Expr:
    """open, high, low and close above zero, high the largest and low the smallest."""
    prices = [pl.col(c) for c in PRICES]
    positive = pl.all_horizontal([p > 0 for p in prices])
    return positive & (pl.col("high") >= pl.max_horizontal(prices)) & (pl.col("low") <= pl.min_horizontal(prices))


def _problem(rule: str, rows: pl.DataFrame) -> Problem:
    dates = rows["date"].cast(pl.String).fill_null("") if rows.height else []
    return Problem(rule, rows.height, list(zip(rows["code"].fill_null("")[:SHOWN], list(dates)[:SHOWN])))


def _without_list_date(bars: pl.DataFrame, securities: pl.DataFrame | None) -> Problem:
    codes = bars.select("code").unique()
    if securities is not None and {"code", "list_date"} <= set(securities.columns):
        dated = securities.filter(
            pl.col("list_date").str.strip_chars().str.to_date("%Y-%m-%d", strict=False).is_not_null()
        )
        codes = codes.join(dated.select("code"), on="code", how="anti")
    return Problem("no_list_date", codes.height, [(c, "") for c in codes["code"].sort()[:SHOWN]])


def _li(column: str) -> pl.Expr:
    return (pl.col(column) * 1000).round().cast(pl.Int32)


def _stored_bars(bars: pl.DataFrame) -> pl.DataFrame:
    """bars as the store keeps them: prices in 厘, preclose given or the previous close (preclose_derived)."""
    given = "preclose" in bars.columns
    previous = pl.col("close").shift(1).over("code")
    frame = bars.with_columns(
        *(_li(c) for c in PRICES),
        preclose=_li("preclose") if given else (previous * 1000).round().cast(pl.Int32),
        preclose_derived=pl.lit(not given),
        volume=pl.col("volume").round().cast(pl.Int64),
        turn=pl.lit(None, pl.Float64),
        tradestatus=_flag("tradestatus", bars).cast(pl.Int8),
        is_st=_flag("st", bars).cast(pl.Boolean),
        source=pl.lit(SOURCE),
    )
    return frame.select(list(TABLES["bars"].schema)).cast(TABLES["bars"].schema)


def _flag(column: str, bars: pl.DataFrame) -> pl.Expr:
    if column not in bars.columns:
        return pl.lit(None, pl.Int8)
    return pl.col(column).str.strip_chars().cast(pl.Int8, strict=False)


def _status(bars: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    """daily_status from st and tradestatus when the file has them; empty otherwise (ST then reads as unknown)."""
    if "st" not in columns and "tradestatus" not in columns:
        return pl.DataFrame(schema=TABLES["daily_status"].schema)
    frame = bars.select(
        "code",
        "date",
        tradestatus=_flag("tradestatus", bars).fill_null(1).cast(pl.Int8),
        is_st=_flag("st", bars).cast(pl.Boolean),
        source=pl.lit(SOURCE),
    )
    return frame.filter(pl.col("is_st").is_not_null() | ("st" not in columns)).cast(TABLES["daily_status"].schema)


def _factors(bars: pl.DataFrame, given: bool) -> pl.DataFrame:
    """A row at each code's first bar and wherever its factor changes; 1.0 throughout without a factor column."""
    factor = pl.col("factor") if given else pl.lit(1.0)
    frame = bars.select("code", ex_date=pl.col("date"), factor=factor.fill_null(strategy="forward").over("code"))
    changed = (pl.col("factor") != pl.col("factor").shift(1).over("code")).fill_null(True)
    return frame.filter(changed & pl.col("factor").is_not_null()).cast(TABLES["adj_factor"].schema)


def _securities(bars: pl.DataFrame, given: pl.DataFrame | None) -> pl.DataFrame:
    """One row per code: exchange and board from the code, first and last bar, and the file's name and dates."""
    spans = bars.group_by("code").agg(first_date=pl.col("date").min(), last_date=pl.col("date").max()).sort("code")
    frame = spans.with_columns(
        exchange=pl.col("code").str.slice(7, 2),
        board=pl.col("code").map_elements(_board, return_dtype=pl.String),
        source=pl.lit(SOURCE),
    )
    if given is not None and set(SECURITY_COLUMNS) <= set(given.columns):
        dated = given.select(
            "code",
            name=pl.col("name"),
            list_date=pl.col("list_date").str.to_date("%Y-%m-%d", strict=False),
            delist_date=pl.col("delist_date").str.to_date("%Y-%m-%d", strict=False),
        ).unique("code", keep="first")
        frame = frame.join(dated, on="code", how="left")
    else:
        frame = frame.with_columns(
            name=pl.lit(None, pl.String), list_date=pl.lit(None, pl.Date), delist_date=pl.lit(None, pl.Date)
        )
    return frame.select(list(TABLES["securities"].schema)).cast(TABLES["securities"].schema)


def _board(code: str) -> str | None:
    try:
        return board(code)
    except ValueError:
        return None


def _issues(raw: pl.DataFrame, bars: pl.DataFrame) -> pl.DataFrame:
    """data_issues of the dropped rows that have a code and a date, and of the flagged bars."""
    typed = _typed(raw)
    frames = [pl.DataFrame(schema=TABLES["data_issues"].schema)]
    dropped = typed.filter(pl.col("keyed") & ~pl.col("first")).select("code", "date").unique()
    if dropped.height:
        frames.append(issue_rows(dropped, "bars", "own_row_dropped", "row_dropped", [], SOURCE))
    order = bars.filter(~_fits(bars)).select("code", "date")
    if order.height:
        frames.append(issue_rows(order, "bars", "own_price_order", "flagged", [], SOURCE))
    return pl.concat(frames).sort("code", "date")
