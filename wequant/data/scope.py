"""A data root's scope (S2.11): the stocks it holds bars for, as every SH/SZ stock, the stocks that were members of
some offered indexes at some time, a list, or indexes and a list. The manifest keeps it in layers, widest first, each
holding its stocks from its own first day: a wider scope added later reaches back segment by segment."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from wequant.data.codes import is_code
from wequant.data.indexes import LEGACY, OFFERED, stored_as
from wequant.data.sources.baostock import to_baostock


@dataclass(frozen=True)
class Scope:
    """all: every SH/SZ stock; else the members of indexes (some of OFFERED) at any time, and the stocks listed."""

    all: bool = False
    indexes: tuple[str, ...] = ()
    stocks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.all and (self.indexes or self.stocks):
            raise ValueError("a scope of every stock names no index and no stock")
        if not (self.all or self.indexes or self.stocks):
            raise ValueError("a scope needs every stock, an index or a stock")
        if unknown := [i for i in self.indexes if i not in OFFERED]:
            raise ValueError(f"not an offered index: {', '.join(unknown)}")
        if bad := [c for c in self.stocks if not is_code(c)]:
            raise ValueError(f"not a stock code: {', '.join(bad[:5])}")
        object.__setattr__(self, "indexes", tuple(i for i in OFFERED if i in self.indexes))
        object.__setattr__(self, "stocks", tuple(sorted(set(self.stocks))))

    def __or__(self, other: Scope) -> Scope:
        if self.all or other.all:
            return ALL
        return Scope(indexes=(*self.indexes, *other.indexes), stocks=(*self.stocks, *other.stocks))

    def __le__(self, other: Scope) -> bool:
        return other.all or (
            not self.all and set(self.indexes) <= set(other.indexes) and set(self.stocks) <= set(other.stocks)
        )

    def covers(self, code: str) -> bool:
        """The scope holds every stock the strategy scope code (labels.scopes: all, an index) can pick."""
        return self.all or code in self.indexes

    def as_json(self) -> dict:
        return {"all": self.all, "indexes": list(self.indexes), "stocks": list(self.stocks)}

    @classmethod
    def of(cls, found: dict) -> Scope:
        return cls(bool(found.get("all")), tuple(found.get("indexes", ())), tuple(found.get("stocks", ())))


ALL = Scope(all=True)
DEFAULT = Scope(indexes=OFFERED)  # the first build's default: CSI 300 and CSI 500


@dataclass(frozen=True)
class Layer:
    """A scope whose stocks have bars from since on."""

    scope: Scope
    since: dt.date


def layers(manifest: dict, first: dt.date) -> list[Layer]:
    """The manifest's layers, widest first; a data root made before S2.11 (or from the user's own files) holds every
    stock from its first day."""
    found = manifest.get("scope")
    if not found:
        return [Layer(ALL, first)]
    return [Layer(Scope.of(layer), dt.date.fromisoformat(layer["since"])) for layer in found["layers"]]


def as_meta(found: list[Layer]) -> dict:
    """The manifest key that records the layers."""
    return {"scope": {"layers": [{**layer.scope.as_json(), "since": layer.since.isoformat()} for layer in found]}}


def segments(end: dt.date, first: dt.date, years: int) -> list[tuple[dt.date, dt.date]]:
    """The windows from end back to first, newest first, each within `years` calendar years."""
    found = []
    while end >= first:
        start = max(first, dt.date(end.year - years + 1, 1, 1))
        found.append((start, end))
        end = start - dt.timedelta(days=1)
    return found


def at(found: list[Layer], day: dt.date) -> Scope:
    """The stocks the data holds on day: the widest layer begun by then."""
    return next(layer.scope for layer in found if layer.since <= day)


def reach(found: list[Layer], added: Scope) -> dt.date | None:
    """The first day from which the data holds added's stocks; None when no layer does."""
    return min((layer.since for layer in found if added <= layer.scope), default=None)


def widen(found: list[Layer], added: Scope, since: dt.date) -> list[Layer]:
    """The layers once added's stocks have bars from since on: every layer from since on holds them too (one starts on
    since if none does), and a layer with the scope of the one before it folds into it, so no two share a scope."""
    days = sorted({layer.since for layer in found} | {since}, reverse=True)
    merged: list[Layer] = []
    for day in days:  # widest first
        scope = at(found, day) | added if day >= since else at(found, day)
        merged = (
            [*merged[:-1], Layer(scope, day)] if merged and merged[-1].scope == scope else [*merged, Layer(scope, day)]
        )
    return merged


def covers(found: list[Layer]) -> dict[str, dt.date]:
    """For each strategy scope code the data covers (all, the offered indexes, the legacy ones), the first day from
    which it holds every stock that code can pick."""
    since = {}
    for code in ("all", *OFFERED, *LEGACY):
        days = [layer.since for layer in found if layer.scope.covers(code) or (code in LEGACY and layer.scope.all)]
        if days:
            since[code] = min(days)
    return since


def needed(scope: Scope, first: dt.date, last: dt.date, basic: pl.DataFrame, spans: pl.DataFrame) -> list[str]:
    """The stocks scope picks from first to last, in BaoStock's form (sh.600000): every SH/SZ stock listed at some time
    in the window, or the members of its indexes at some time in it and its stocks."""
    if scope.all:
        listed = (pl.col("ipoDate") != "") & (pl.col("ipoDate") <= last.isoformat())
        alive = (pl.col("outDate") == "") | (pl.col("outDate") >= first.isoformat())
        stocks = basic.filter((pl.col("type") == "1") & pl.col("code").str.contains(r"^s[hz]\.") & listed & alive)
        return sorted(stocks["code"])
    members = spans.filter(
        pl.col("index_code").is_in([stored_as(index) for index in scope.indexes])
        & (pl.col("start_date") <= last)
        & (pl.col("end_date") >= first)
    )
    codes = set(members["code"]) | set(scope.stocks)
    return sorted(to_baostock(code) for code in codes)
