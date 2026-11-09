"""The rule book: exchange trading rules by board and effective date, read from rules.toml."""

from __future__ import annotations

import bisect
import datetime as dt
import functools
import hashlib
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from importlib.resources import files
from pathlib import Path

import polars as pl

from wequant.data.codes import BOARDS, EXCHANGES


@dataclass(frozen=True)
class Band:
    id: str
    board: str
    since: dt.date
    normal_bp: int
    st_bp: int
    confirm: str | None = None


@dataclass(frozen=True)
class NewListing:
    id: str
    board: str
    since: dt.date
    free_days: int
    first_up_bp: int | None
    first_down_bp: int | None
    confirm: str | None = None


@dataclass(frozen=True)
class Lot:
    id: str
    board: str
    since: dt.date
    min_qty: int  # shares
    step: int  # shares
    tick: int  # 厘
    confirm: str | None = None


@dataclass(frozen=True)
class Rate:
    id: str
    exchanges: tuple[str, ...]
    since: dt.date
    buy: Fraction
    sell: Fraction
    basis: str  # "amount" | "par"
    confirm: str | None = None


TABLES = ("band", "new_listing", "lot", "stamp_tax", "transfer_fee")
FRAME_COLUMNS = {
    "band": {"normal_bp": pl.Int64, "st_bp": pl.Int64},
    "new_listing": {"free_days": pl.Int32, "first_up_bp": pl.Int64, "first_down_bp": pl.Int64},
    "lot": {"min_qty": pl.Int64, "step": pl.Int64, "tick": pl.Int64},
}

_KEYS = {  # required, optional
    "band": ({"id", "board", "since", "normal"}, {"st", "confirm"}),
    "new_listing": ({"id", "board", "since", "free_days"}, {"first_day_up", "first_day_down", "confirm"}),
    "lot": ({"id", "board", "since", "min_qty", "step", "tick"}, {"confirm"}),
    "stamp_tax": ({"id", "since", "buy", "sell"}, {"confirm"}),
    "transfer_fee": ({"id", "exchanges", "since", "rate", "basis"}, {"confirm"}),
}


@dataclass(frozen=True)
class RuleBook:
    digest: str  # sha256 hex of the TOML bytes
    band: tuple[Band, ...]
    new_listing: tuple[NewListing, ...]
    lot: tuple[Lot, ...]
    stamp_tax: tuple[Rate, ...]
    transfer_fee: tuple[Rate, ...]

    def rules(self) -> tuple:
        """Every rule row of every table."""
        return tuple(row for table in TABLES for row in getattr(self, table))

    def unconfirmed(self) -> tuple:
        """Rows that carry a confirm note."""
        return tuple(row for row in self.rules() if row.confirm)

    def in_force(self, table: str, key: str, day: dt.date):
        """The row of table for board (band/new_listing/lot) or exchange (stamp_tax/transfer_fee) key on day; LookupError if none applies yet."""
        if table not in TABLES:
            raise ValueError(f"unknown rule table {table!r}")
        rows = [row for row in getattr(self, table) if key in _keys(row)]
        index = bisect.bisect_right([row.since for row in rows], day)
        if index == 0:
            raise LookupError(f"no {table} rule for {key} on {day}")
        return rows[index - 1]

    def frame(self, table: str) -> pl.DataFrame:
        """A board-keyed table (band, new_listing, lot) as a polars frame (board, since, <table>_id, fields) for join_asof."""
        if table not in FRAME_COLUMNS:
            raise ValueError(f"{table!r} is not keyed by board")
        columns = FRAME_COLUMNS[table]
        schema = {"board": pl.String, "since": pl.Date, f"{table}_id": pl.String, **columns}
        rows = [(r.board, r.since, r.id, *(getattr(r, c) for c in columns)) for r in getattr(self, table)]
        return pl.DataFrame(rows, schema=schema, orient="row").sort("since")


def load_book(path: Path | None = None) -> RuleBook:
    """Read and validate rules.toml; the packaged file by default."""
    data = Path(path).read_bytes() if path is not None else files("wequant.rules").joinpath("rules.toml").read_bytes()
    raw = tomllib.loads(data.decode("utf-8"), parse_float=Decimal)
    if unknown := sorted(set(raw) - set(TABLES)):
        raise ValueError(f"unknown rule tables {unknown}")
    tables = {table: tuple(_parse(table, row) for row in raw.get(table, [])) for table in TABLES}
    book = RuleBook(hashlib.sha256(data).hexdigest(), **tables)
    _check_ids(book)
    _check_order(book)
    return book


@functools.cache
def default_book() -> RuleBook:
    """The packaged rule book, loaded once."""
    return load_book()


def _keys(row) -> tuple[str, ...]:
    return row.exchanges if isinstance(row, Rate) else (row.board,)


def _parse(table: str, row: dict):
    where = f"{table} {row.get('id', '<no id>')}"
    required, optional = _KEYS[table]
    if missing := sorted(required - row.keys()):
        raise ValueError(f"{where}: missing {missing}")
    if unknown := sorted(row.keys() - required - optional):
        raise ValueError(f"{where}: unknown keys {unknown}")
    try:
        return _PARSERS[table](row)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{where}: {error}") from None


def _band(row: dict) -> Band:
    normal = _bp(row["normal"])
    st = _bp(row["st"]) if "st" in row else normal
    return Band(row["id"], _board(row["board"]), _date(row["since"]), normal, st, row.get("confirm"))


def _new_listing(row: dict) -> NewListing:
    free_days = _count(row["free_days"], "free_days", 0)
    first = [row[k] for k in ("first_day_up", "first_day_down") if k in row]
    if len(first) == 1:
        raise ValueError("first_day_up and first_day_down go together")
    if first and free_days:
        raise ValueError("a first-day band needs free_days 0")
    up, down = (_bp(first[0]), _bp(first[1])) if first else (None, None)
    return NewListing(row["id"], _board(row["board"]), _date(row["since"]), free_days, up, down, row.get("confirm"))


def _lot(row: dict) -> Lot:
    min_qty, step = _count(row["min_qty"], "min_qty", 1), _count(row["step"], "step", 1)
    if step > min_qty:
        raise ValueError("step exceeds min_qty")
    tick = _count(row["tick"], "tick", 1)
    return Lot(row["id"], _board(row["board"]), _date(row["since"]), min_qty, step, tick, row.get("confirm"))


def _stamp_tax(row: dict) -> Rate:
    buy, sell = _rate(row["buy"]), _rate(row["sell"])
    return Rate(row["id"], EXCHANGES, _date(row["since"]), buy, sell, "amount", row.get("confirm"))


def _transfer_fee(row: dict) -> Rate:
    rate = _rate(row["rate"])
    if row["basis"] not in ("amount", "par"):
        raise ValueError(f"basis {row['basis']!r} is not amount or par")
    exchanges = tuple(row["exchanges"])
    if not exchanges or not set(exchanges) <= set(EXCHANGES):
        raise ValueError(f"exchanges {list(exchanges)} are not among {EXCHANGES}")
    return Rate(row["id"], exchanges, _date(row["since"]), rate, rate, row["basis"], row.get("confirm"))


_PARSERS = {
    "band": _band,
    "new_listing": _new_listing,
    "lot": _lot,
    "stamp_tax": _stamp_tax,
    "transfer_fee": _transfer_fee,
}


def _bp(value) -> int:
    bp = Decimal(value) * 10000
    if bp != bp.to_integral_value() or not 0 < bp < 10000:
        raise ValueError(f"band {value} is not a whole number of basis points between 0 and 1")
    return int(bp)


def _rate(value) -> Fraction:
    rate = Fraction(Decimal(value))
    if not 0 <= rate < 1:
        raise ValueError(f"rate {value} is not in [0, 1)")
    return rate


def _count(value, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} {value!r} is not an integer >= {minimum}")
    return value


def _board(value: str) -> str:
    if value not in BOARDS:
        raise ValueError(f"board {value!r} is not among {BOARDS}")
    return value


def _date(value) -> dt.date:
    if type(value) is not dt.date:
        raise ValueError(f"since {value!r} is not a date")
    return value


def _check_ids(book: RuleBook) -> None:
    seen = set()
    for table in TABLES:
        for row in getattr(book, table):
            if row.id in seen:
                raise ValueError(f"{table} {row.id}: duplicate id")
            seen.add(row.id)


def _check_order(book: RuleBook) -> None:
    for table in TABLES:
        last: dict[str, dt.date] = defaultdict(lambda: dt.date.min)
        for row in getattr(book, table):
            for key in _keys(row):
                if row.since <= last[key]:
                    raise ValueError(f"{table} {row.id}: since {row.since} does not follow the previous {key} row")
                last[key] = row.since
