"""Every wequant table, column and type, defined once.

Prices are int32 厘 (0.001 元; index points × 1000), volume is int64 shares and amount is
float64 yuan.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class Column:
    name: str
    dtype: pl.DataType
    nullable: bool = False
    doc: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    year_partition: str | None = None  # date column whose year selects the file, or None
    doc: str = ""

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return {c.name: c.dtype for c in self.columns}

    def validate(self, df: pl.DataFrame) -> None:
        """Raise ValueError unless df has exactly this table's columns, types and non-null columns."""
        if dict(df.schema) != self.schema:
            raise ValueError(f"{self.name}: schema {dict(df.schema)} != {self.schema}")
        for column in self.columns:
            if not column.nullable and df[column.name].null_count():
                raise ValueError(f"{self.name}.{column.name} has nulls")


def _price(name: str, nullable: bool = False) -> Column:
    return Column(name, pl.Int32, nullable, "厘 (0.001 元)")


SECURITIES = Table(
    "securities",
    (
        Column("code", pl.String, doc="600000.SH / 000001.SZ / 920047.BJ"),
        Column("exchange", pl.String, doc="SH / SZ / BJ"),
        Column("board", pl.String, doc="main / chinext / star / bse, from the code prefix"),
        Column(
            "first_date",
            pl.Date,
            doc="first day the source has data for this code; NOT the official listing date",
        ),
        Column(
            "last_date",
            pl.Date,
            doc="last day the source has data for this code; NOT the official delisting date",
        ),
        Column("name", pl.String, nullable=True, doc="security name; null until a name source is added"),
        Column("source", pl.String, doc="<source>@<version> the rows came from"),
    ),
    key=("code",),
    doc="Stocks only. Indices are in index_bars.",
)

CALENDAR = Table(
    "calendar",
    (Column("date", pl.Date, doc="trading day, up to the last day that has data"),),
    key=("date",),
)

BARS = Table(
    "bars",
    (
        Column("code", pl.String),
        Column("date", pl.Date),
        _price("open"),
        _price("high"),
        _price("low"),
        _price("close"),
        Column("volume", pl.Int64, doc="shares (股)"),
        Column("amount", pl.Float64, doc="yuan (元)"),
        _price("preclose", nullable=True),
        Column(
            "preclose_derived",
            pl.Boolean,
            doc="true: preclose computed from the previous close and the adjustment ratio, "
            "not taken from an exchange-published previous close",
        ),
        Column("tradestatus", pl.Int8, nullable=True, doc="null until a source provides it"),
        Column("is_st", pl.Boolean, nullable=True, doc="null until a source provides it"),
        Column("source", pl.String),
    ),
    key=("code", "date"),
    year_partition="date",
    doc="Unadjusted daily stock bars. Days without trading have no row.",
)

ADJ_FACTOR = Table(
    "adj_factor",
    (
        Column("code", pl.String),
        Column("ex_date", pl.Date, doc="first day the factor applies"),
        Column(
            "factor",
            pl.Float64,
            doc="back-adjustment ratio = adjusted close / raw close, valid from ex_date "
            "until the next row of the same code",
        ),
    ),
    key=("code", "ex_date"),
    doc="One row on each code's first day with data, then one per adjustment event.",
)

INDEX_BARS = Table(
    "index_bars",
    (
        Column("code", pl.String),
        Column("date", pl.Date),
        Column("open", pl.Int32, doc="0.001 index points"),
        Column("high", pl.Int32, doc="0.001 index points"),
        Column("low", pl.Int32, doc="0.001 index points"),
        Column("close", pl.Int32, doc="0.001 index points"),
        Column("volume", pl.Int64, doc="shares (股)"),
        Column("amount", pl.Float64, doc="yuan (元)"),
        Column("source", pl.String),
    ),
    key=("code", "date"),
)

INDEX_MEMBERS = Table(
    "index_members",
    (
        Column("index_code", pl.String, doc="e.g. 000905.SH"),
        Column("code", pl.String, doc="member stock"),
        Column("start_date", pl.Date),
        Column("end_date", pl.Date),
    ),
    key=("index_code", "code", "start_date"),
)

TABLES: dict[str, Table] = {
    t.name: t for t in (SECURITIES, CALENDAR, BARS, ADJ_FACTOR, INDEX_BARS, INDEX_MEMBERS)
}
