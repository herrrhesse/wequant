"""Raw BaoStock day snapshots: read, checked against the conventions the merge relies on, and split."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import polars as pl

from wequant.data import codes
from wequant.data.raw import BaoStockRaw
from wequant.data.schema import BARS, DAILY_STATUS
from wequant.data.sources.baostock import parse_daily

PRICES = ("open", "high", "low", "close", "preclose")
COLUMNS = ("date", "code", *PRICES, "volume", "amount", "tradestatus", "isST", "_baostock_version")
SAMPLES = 20
AVERAGE_RTOL = 1e-3  # a clean row's amount / volume strays less than 1e-4 beyond [low, high]
AMOUNT_ATOL = 1.0  # yuan: some amounts are cut to the whole yuan

TEXT_CHECKS = {
    "code": pl.col("code").str.contains(r"^s[hz][.][0-9]{6}$"),
    "date": pl.col("date") == pl.col("file"),
    "tradestatus": pl.col("tradestatus").is_in(["0", "1"]),
    "isST": pl.col("isST").is_in(["0", "1"]),
    "price": pl.all_horizontal(pl.col(p).str.contains(r"^([0-9]+[.][0-9]{2}(00)?)?$") for p in PRICES),
    "volume": pl.col("volume").str.contains(r"^[0-9]*$"),
    "amount": pl.col("amount").str.contains(r"^([0-9]+([.][0-9]+)?)?$"),
}

_TRADED = (pl.col("volume") > 0).fill_null(False)
_SANE = (
    pl.all_horizontal(pl.col(c).is_not_null() for c in (*PRICES, "amount"))
    & (pl.col("preclose") > 0)
    & (pl.col("low") > 0)
    & (pl.col("low") <= pl.min_horizontal("open", "close"))
    & (pl.max_horizontal("open", "close") <= pl.col("high"))
)


def _value(price: str) -> pl.Expr:
    """The day's volume at a price column, in yuan."""
    return pl.col(price) * pl.col("volume") / 1000


_PAID = (
    (pl.col("amount") > 0)
    & (pl.col("amount") >= _value("low") * (1 - AVERAGE_RTOL) - AMOUNT_ATOL)
    & (pl.col("amount") <= _value("high") * (1 + AVERAGE_RTOL) + AMOUNT_ATOL)
)


class RawDataError(ValueError):
    """Raw rows break a convention the merge relies on; nothing is written."""

    def __init__(self, problems: list[str]):
        super().__init__("raw rows break merge conventions:\n" + "\n".join(problems))
        self.problems = problems


def read_daily(raw: BaoStockRaw, days: Iterable[dt.date]) -> pl.DataFrame:
    """DAILY columns plus source ('baostock@<_baostock_version>') for these days' snapshots; RawDataError lists malformed rows."""
    paths = [str(raw.daily(day)) for day in sorted(days)]
    scan = pl.scan_parquet(paths, include_file_paths="path")
    frame = scan.select(*COLUMNS, file=pl.col("path").str.extract(r"([^/\\]+)\.parquet$"))
    _raise_on(_failures(frame, TEXT_CHECKS))
    source = frame.select(source=pl.lit("baostock@") + pl.col("_baostock_version"))
    daily = pl.concat([parse_daily(frame), source], how="horizontal").collect(engine="streaming")
    _raise_on(_failures(daily.lazy(), _row_checks(daily)))
    return daily


def split(daily: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(bars rows of rows with volume > 0, daily_status rows of every row)."""
    bars = daily.filter(_TRADED).with_columns(preclose_derived=pl.lit(False)).select(list(BARS.schema))
    return bars, daily.select(list(DAILY_STATUS.schema))


def _row_checks(daily: pl.DataFrame) -> dict[str, pl.Expr]:
    not_stock = [c for c in daily["code"].unique().to_list() if not _is_stock(c)]
    return {
        "code": ~pl.col("code").is_in(not_stock),
        "unique": ~pl.col("code").is_duplicated().over("date"),
        "traded": ~_TRADED | _SANE,
        "average_price": ~_TRADED | _PAID,
    }


def _is_stock(code: str) -> bool:
    try:
        return codes.security_type(code) == "stock"
    except ValueError:
        return False


def _failures(frame: pl.LazyFrame, checks: dict[str, pl.Expr]) -> list[str]:
    """One line per failing check: its row count and up to SAMPLES offending rows."""
    oks = {name: ok.fill_null(False) for name, ok in checks.items()}
    counts = frame.select(**{name: (~ok).sum() for name, ok in oks.items()}).collect(engine="streaming")
    counts = counts.row(0, named=True)
    lines = []
    for name, count in counts.items():
        if count:
            samples = frame.filter(~oks[name]).drop("source", strict=False).head(SAMPLES).collect()
            lines.append(f"{name}: {count} rows, e.g. {samples.rows()}")
    return lines


def _raise_on(problems: list[str]) -> None:
    if problems:
        raise RawDataError(problems)
