"""Every wequant table, column and type, defined once.

Prices are int32 厘 (0.001 元; index points × 1000), volume is int64 shares and amount is
float64 yuan; a null bars amount is unknown, never zero.
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
    date_column: str | None = None  # the column date-range reads filter on
    partitioned: bool = False  # one file per year of date_column
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
    doc="Stocks only; indices are in index_bars. Every code in bars, adj_factor and daily_status has a row here.",
)

CALENDAR = Table(
    "calendar",
    (Column("date", pl.Date, doc="trading day, up to the last day that has data"),),
    key=("date",),
    date_column="date",
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
        Column("amount", pl.Float64, nullable=True, doc="yuan (元); null: unknown (a resolution voided it), never 0"),
        Column(
            "turn", pl.Float64, nullable=True, doc="turnover in percent of tradable shares, BaoStock's; null elsewhere"
        ),
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
    date_column="date",
    partitioned=True,
    doc="Unadjusted daily stock bars, one row per traded day. "
    "tradestatus and is_st repeat daily_status on these days; null where no source provides them.",
)

DAILY_STATUS = Table(
    "daily_status",
    (
        Column("code", pl.String),
        Column("date", pl.Date),
        Column("tradestatus", pl.Int8, doc="1 traded, 0 suspended, as the source reports it"),
        Column("is_st", pl.Boolean, doc="ST or *ST that day"),
        Column("source", pl.String, doc="<source>@<version>"),
    ),
    key=("code", "date"),
    date_column="date",
    partitioned=True,
    doc="One row per listed day of each SH/SZ stock, suspended days included. No row: status unknown.",
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
    date_column="ex_date",
    doc="One row on each code's first day with data, then one per adjustment event. A code without rows has factor 1.0.",
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
    date_column="date",
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

DATA_ISSUES = Table(
    "data_issues",
    (
        Column("table", pl.String, doc="affected table: bars / daily_status / adj_factor / securities / index_members"),
        Column("code", pl.String, doc="affected code; a malformed code is kept as found, e.g. SHT00018"),
        Column("date", pl.Date, doc="affected day; for an index member span, its start_date"),
        Column(
            "kind",
            pl.String,
            doc="off_scale / removed_security / adj_blip / invalid_member_code / bar_added / bar_unconfirmed / "
            "duplicate_code / price_mismatch / average_price / close_unconfirmed / volume_mismatch / amount_mismatch / "
            "preclose_mismatch / tradestatus_conflict / before_first_bar / event_unconfirmed / event_added / "
            "step_mismatch / event_missing / event_unimplied / event_step_off / no_bars / limit_breach; "
            "each kind belongs to one table",
        ),
        Column(
            "action",
            pl.String,
            doc="row_dropped (on bars: a delivered bar the store does not keep; the day reads as missing data unless "
            "daily_status reports it suspended) / security_removed / excluded_from_adj_factor_and_preclose / "
            "span_dropped / row_added / duplicate_dropped / value_replaced / bar_kept / no_bar / status_dropped / "
            "event_dropped / event_added / flagged (nothing changed; to review) / limits_voided (limits unknown that day) / "
            "resolved_baostock / resolved_seed / resolved_null (an approved resolution took BaoStock's values, the "
            "store's, or voided the amount; detail has its fields, the store's values before the cell's first resolution "
            "(null without a store bar), BaoStock's values, the values after and the evidence)",
        ),
        Column("detail", pl.String, nullable=True, doc="JSON object with the measured values"),
        Column("source", pl.String),
    ),
    key=("table", "code", "date", "kind"),
    date_column="date",
    doc="Data a source delivered that was dropped, changed or flagged, so a gap is never mistaken for a suspension. "
    "State kinds (event_missing / event_unimplied / event_step_off / limit_breach) are replaced by their writer on every run.",
)

TABLES: dict[str, Table] = {
    t.name: t for t in (SECURITIES, CALENDAR, BARS, DAILY_STATUS, ADJ_FACTOR, INDEX_BARS, INDEX_MEMBERS, DATA_ISSUES)
}
