"""Every wequant table, column and type, and every data_issues kind, defined once.

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
        Column(
            "list_date", pl.Date, nullable=True, doc="official listing date (BaoStock's ipoDate); null when unknown"
        ),
        Column(
            "delist_date",
            pl.Date,
            nullable=True,
            doc="official delisting date (BaoStock's outDate); null while listed or when unknown",
        ),
        Column("name", pl.String, nullable=True, doc="the security's current name; null when no source gave one"),
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

AUCTION = "auction trading only, as BaoStock and the data vendors count it: block and after-hours trades excluded"
BARS = Table(
    "bars",
    (
        Column("code", pl.String),
        Column("date", pl.Date),
        _price("open"),
        _price("high"),
        _price("low"),
        _price("close"),
        Column("volume", pl.Int64, doc=f"shares (股); {AUCTION}"),
        Column(
            "amount",
            pl.Float64,
            nullable=True,
            doc=f"yuan (元); {AUCTION}; null: unknown (a resolution voided it), never 0",
        ),
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

ANOMALY_VERSION = 1  # bump when a kind's class or level, or what excluding a level does, changes meaning
CLASSES = ("price", "volume", "bar", "gap", "limits", "status", "adjustment")
CLASS_DOCS = {  # what an anomaly taints in its cell
    "price": "open, high, low, close, preclose, their adjusted values and the limits derived from the preclose",
    "volume": "volume and amount",
    "bar": "every field of the bar, which has one source only",
    "gap": "nothing is there: the day reads as missing data",
    "limits": "the day's price limits",
    "status": "the day's status, and so whether it traded",
    "adjustment": "the adjustment ratio across the day",
}
LEVELS = ("error", "suspicious", "notice")
LEVEL_DOCS = {
    "error": "the data contradicted the exchange's and was corrected or dropped by an approved, evidenced resolution",
    "suspicious": "a source contradicts itself or another source; not checked against the exchange",
    "notice": "a difference of definition, or data that is right where a rule is not modelled",
}
NOT_ANOMALOUS_ACTIONS = ("security_removed", "span_dropped", "duplicate_dropped", "status_dropped")


@dataclass(frozen=True)
class IssueKind:
    """A data_issues kind: its table and actions, what it means, and as an anomaly the class of data it taints in its
    (code, date) cell and its level; state kinds are replaced by their writer on every run, record kinds kept."""

    table: str
    actions: tuple[str, ...]
    doc: str
    anomaly: str | None = None  # one of CLASSES; None: no cell a run reads is affected
    level: str | None = None  # one of LEVELS, set exactly for an anomaly
    state: bool = False


def _kind(
    table: str, actions: str, doc: str, anomaly: str | None = None, level: str | None = None, state: bool = False
) -> IssueKind:
    return IssueKind(table, tuple(actions.split()), doc, anomaly, level, state)


ISSUE_KINDS: dict[str, IssueKind] = {
    "own_row_dropped": _kind(
        "bars",
        "row_dropped",
        "a row of the user's own file whose numbers cannot be read, or that repeats an earlier row's code and date; "
        "dropped",
        "gap",
        "suspicious",
    ),
    "own_price_order": _kind(
        "bars",
        "flagged",
        "a bar of the user's own file whose open, high, low and close do not fit together (a price not above zero, "
        "high below another price or low above one); kept as given",
        "price",
        "error",
    ),
    "off_scale": _kind(
        "bars", "row_dropped", "the seed's amount and volume disagree with its price; bar dropped", "gap", "suspicious"
    ),
    "removed_security": _kind("bars", "security_removed", "every bar of the code was off scale; code dropped"),
    "adj_blip": _kind(
        "adj_factor",
        "excluded_from_adj_factor_and_preclose",
        "a spike in the seed's adjusted price, bypassed by the factor and the derived preclose",
        "adjustment",
        "suspicious",
    ),
    "invalid_member_code": _kind("index_members", "span_dropped", "a member code that is no stock; span dropped"),
    "bar_added": _kind("bars", "row_added", "a bar only BaoStock has: one source for the whole bar", "bar", "notice"),
    "bar_unconfirmed": _kind("bars", "row_dropped", "a seed bar BaoStock lacks; dropped", "gap", "suspicious"),
    "duplicate_code": _kind("bars", "duplicate_dropped", "a successor code's copy of another code's bars; dropped"),
    "price_mismatch": _kind(
        "bars",
        "resolved_baostock resolved_seed resolved_corrected",
        "the sources' open, high, low or close disagree; an approved, evidenced resolution corrected BaoStock's",
        "price",
        "error",
    ),
    "average_price": _kind(
        "bars",
        "resolved_seed resolved_null resolved_corrected",
        "amount over volume outside the bar's range; an approved resolution took the seed's, voided or corrected it",
        "volume",
        "error",
    ),
    "amount_carried": _kind(
        "bars",
        "resolved_baostock resolved_seed resolved_null resolved_corrected",
        "BaoStock's amount repeats the code's previous row while the volume differs; an approved resolution corrected it",
        "volume",
        "error",
    ),
    "stale_volume": _kind(
        "bars",
        "resolved_no_bar resolved_baostock resolved_corrected",
        "a suspended row (tradestatus 0, amount 0) carried an old volume; an approved resolution built no bar",
        "status",
        "error",
    ),
    "volume_truncated": _kind(
        "bars",
        "resolved_baostock resolved_seed resolved_corrected",
        "BaoStock's snapshot of the day was cut before the close; an approved resolution corrected volume and amount",
        "volume",
        "error",
    ),
    "preclose_odd": _kind(
        "bars",
        "resolved_baostock resolved_corrected",
        "BaoStock's exchange preclose is not a whole fen; an approved resolution decided it",
        "price",
        "error",
    ),
    **{
        f"{data}_{state}": _kind(
            "bars",
            "resolved_baostock resolved_seed resolved_null resolved_no_bar resolved_corrected",
            f"an approved resolution decided {data} {doc}",
            "price" if data == "price" else "volume",
            level,
        )
        for data in ("price", "volume")
        for state, doc, level in (
            ("noted", "where the verified value is the one kept: noted only", "notice"),
            ("unverified", "on internal evidence only, without an outside check", "suspicious"),
        )
    },
    "close_preclose_off": _kind(
        "bars",
        "flagged",
        "the close is not the next day's exchange preclose and no adjustment explains it",
        "price",
        "suspicious",
        True,
    ),
    "snapshot_truncated": _kind(
        "bars",
        "flagged",
        "the source's snapshot of the day looks cut before the close: many of its closes are not the next day's "
        "exchange preclose, so its volumes and amounts may fall short",
        "volume",
        "suspicious",
        True,
    ),
    "volume_mismatch": _kind(
        "bars", "value_replaced", "the sources' volumes differ; BaoStock's kept", "volume", "suspicious"
    ),
    "amount_mismatch": _kind(
        "bars", "value_replaced", "the sources' amounts differ; BaoStock's kept", "volume", "suspicious"
    ),
    "preclose_mismatch": _kind(
        "bars",
        "value_replaced",
        "BaoStock's exchange preclose differs from the store's; replaced",
        "price",
        "suspicious",
    ),
    "tradestatus_conflict": _kind(
        "daily_status",
        "bar_kept no_bar",
        "the trade status disagrees with whether a bar exists",
        "status",
        "suspicious",
    ),
    "before_first_bar": _kind("daily_status", "status_dropped", "a status row before the code's first bar; dropped"),
    "event_unconfirmed": _kind(
        "adj_factor", "event_dropped", "a seed adjustment event BaoStock lacks; dropped", "adjustment", "suspicious"
    ),
    "event_added": _kind("adj_factor", "event_added", "an adjustment event only BaoStock has", "adjustment", "notice"),
    "step_mismatch": _kind(
        "adj_factor", "value_replaced", "the sources' adjustment steps differ; replaced", "adjustment", "suspicious"
    ),
    **{
        kind: _kind(
            "adj_factor",
            "flagged",
            f"the exchange preclose and the factor disagree: {doc}",
            "adjustment",
            "suspicious",
            True,
        )
        for kind, doc in (
            ("event_missing", "an ex-date the factor lacks"),
            ("event_unimplied", "a factor step the preclose does not imply"),
            ("event_step_off", "a factor step of another size"),
        )
    },
    "no_bars": _kind("securities", "security_removed", "a code without bars; dropped"),
    "limit_breach": _kind(
        "bars",
        "limits_voided",
        "a bar outside its price limits; the day's limits read as unknown",
        "limits",
        "notice",
        True,
    ),
}
ACTION_NOTES = {
    "row_dropped": "on bars: a delivered bar the store does not keep; the day reads as missing data unless "
    "daily_status reports it suspended",
    "flagged": "nothing changed; to review",
    "limits_voided": "limits unknown that day",
    "resolved_null": "resolved_*: an approved resolution took BaoStock's values, the store's, values corrected from "
    "verification records, voided the amount or built no bar; detail has its fields, the store's values before the "
    "cell's first resolution (null without a store bar), BaoStock's values, the values after, the category, level, "
    "verification ids and the evidence",
}
_ACTIONS = dict.fromkeys(action for kind in ISSUE_KINDS.values() for action in kind.actions)
_STATE_KINDS = [name for name, kind in ISSUE_KINDS.items() if kind.state]

DATA_ISSUES = Table(
    "data_issues",
    (
        Column("table", pl.String, doc="affected table: bars / daily_status / adj_factor / securities / index_members"),
        Column("code", pl.String, doc="affected code; a malformed code is kept as found, e.g. SHT00018"),
        Column("date", pl.Date, doc="affected day; for an index member span, its start_date"),
        Column("kind", pl.String, doc=" / ".join(ISSUE_KINDS) + "; each kind belongs to one table (ISSUE_KINDS)"),
        Column(
            "action",
            pl.String,
            doc=" / ".join(f"{a} ({ACTION_NOTES[a]})" if a in ACTION_NOTES else a for a in _ACTIONS),
        ),
        Column("detail", pl.String, nullable=True, doc="JSON object with the measured values"),
        Column("source", pl.String),
    ),
    key=("table", "code", "date", "kind"),
    date_column="date",
    doc="Data a source delivered that was dropped, changed or flagged, so a gap is never mistaken for a suspension. "
    f"State kinds ({' / '.join(_STATE_KINDS)}) are replaced by their writer on every run.",
)

TABLES: dict[str, Table] = {
    t.name: t for t in (SECURITIES, CALENDAR, BARS, DAILY_STATUS, ADJ_FACTOR, INDEX_BARS, INDEX_MEMBERS, DATA_ISSUES)
}
