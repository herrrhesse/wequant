"""Data scopes through the service (S2.11): the scope a build or an extension asks for, the layers data_status shows,
their Chinese names, and the warnings for a strategy whose scope reaches beyond the data."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, NamedTuple

import polars as pl
from pydantic import model_validator
from pydantic_core import PydanticCustomError

from wequant.data.build import members_floor
from wequant.data.indexes import OFFERED
from wequant.data.raw import BaoStockRaw
from wequant.data.scope import Layer, Scope, at, covers, layers, needed, reach, segments
from wequant.data.sources.baostock import to_baostock
from wequant.data.store import Store
from wequant.service.base import Code, Distinct, Model
from wequant.service.labels import LEGACY_SCOPES, SCOPES
from wequant.service.outputs import Warning
from wequant.strategy import Strategy

ALL_STOCKS = {1: 5_250, 3: 5_330, 5: 5_418}  # SH/SZ stocks listed in the last 1, 3, 5 years (stock_basic, 2026-11-14)
MEMBER_STOCKS = {  # stocks that were members at some time in the last 1, 3, 5 years: PLACEHOLDER until S2.11 measures
    ("000300.SH",): {1: 330, 3: 400, 5: 470},
    ("000905.SH",): {1: 560, 3: 700, 5: 850},
    ("000300.SH", "000905.SH"): {1: 880, 3: 1_080, 5: 1_280},
}
DATA_SCOPE_TEXTS = {  # the English text of each data scope warning, filled from its params
    "scope_beyond_data": lambda p: f"the data holds only {p['data']}; {p['scope']} picks among those stocks alone",
    "scope_beyond_data_before": lambda p: (
        f"before {p['since']} the data holds only {p['data']}; {p['scope']} picks among those stocks alone there"
    ),
}


class DataScope(Model):
    """What a data root holds bars for: every SH/SZ stock (all), or the stocks that were members of some offered
    indexes at any time in its window, and a list of stocks."""

    all: bool = False
    indexes: Annotated[list[Literal[OFFERED]], Distinct] = []
    """Some of labels.scopes' offered index codes: 000300.SH, 000905.SH."""
    stocks: Annotated[list[Code], Distinct] = []
    """Codes such as 600519.SH."""

    @model_validator(mode="after")
    def _some(self) -> DataScope:
        if self.all and (self.indexes or self.stocks):
            raise PydanticCustomError("requires", "a scope of every stock names no index and no stock")
        if not (self.all or self.indexes or self.stocks):
            raise PydanticCustomError("requires", "a scope needs every stock, an index or a stock")
        return self

    def scope(self) -> Scope:
        return Scope(self.all, tuple(self.indexes), tuple(self.stocks))

    @classmethod
    def of(cls, scope: Scope) -> DataScope:
        return cls(all=scope.all, indexes=list(scope.indexes), stocks=list(scope.stocks))


def default_scope() -> DataScope:
    """The first build's default: CSI 300 and CSI 500."""
    return DataScope(indexes=list(OFFERED))


class ScopeLayer(DataScope):
    """Stocks of a scope that have bars from since on."""

    label: str
    """In Chinese, e.g. 沪深300 + 中证500, 沪深全部, 沪深300 + 自定义名单 12 只."""
    since: dt.date


class ScopeCover(Model):
    """A strategy scope the data covers (labels.scopes' code) and the first day it holds every stock that scope picks."""

    code: str
    since: dt.date


class BackfillFloor(Model):
    """Where earlier history stops: the first member list of an index of the oldest layer's scope."""

    index: str
    name: str
    date: dt.date


class ScopeStatus(Model):
    """The data's scope: its layers, widest first, and what they cover."""

    layers: list[ScopeLayer]
    """One layer for a data root built once; a wider scope added later is a layer of its own until it reaches back
    to the next one. Data roots made before S2.11, merged or imported, hold every stock (all) from their first day."""
    covers: list[ScopeCover]
    """The strategy scopes covered (all, the offered indexes, the legacy ones), each from its first day; a scope not
    listed is not covered. A list of stocks is checked by itself (stocks_not_in_data)."""
    stock_count: int
    """Stocks with bars in this version."""
    first_date: dt.date
    backfill_floor: BackfillFloor | None
    """Set once the history reaches the first member list of an index the oldest layer names: data_backfill adds no
    earlier history (labels.job_stages backfill_floor says why); a wider scope would."""


def scope_label(scope: Scope) -> str:
    """The Chinese name of a data scope; the same in layers, warnings and job params."""
    if scope.all:
        return SCOPES["all"][0]
    names = [SCOPES[index][0] for index in scope.indexes]
    if scope.stocks:
        names.append(f"{SCOPES['stocks'][0]} {len(scope.stocks)} 只")
    return " + ".join(names)


def strategy_scope(strategy: Strategy) -> str | None:
    """The strategy's scope as a labels.scopes code (all, an index); None for a list of stocks."""
    pool = strategy.pool
    if pool.stocks is not None:
        return None
    return pool.index or "all"


def held_layers(store: Store, found: dict) -> list[Layer]:
    """The layers of a store version."""
    first = store.read_table("calendar", columns=["date"], manifest=found)["date"].min()
    return layers(found, first)


def beyond_data(strategy: Strategy, store: Store, found: dict, start: dt.date | None) -> list[Warning]:
    """The data scope warnings of a strategy on a store version (start as beyond_warnings takes it)."""
    return beyond_warnings(strategy, held_layers(store, found), start)


def beyond_warnings(strategy: Strategy, held: list[Layer], start: dt.date | None) -> list[Warning]:
    """scope_beyond_data when no layer covers the strategy's scope; scope_beyond_data_before when the layer that does
    starts after start (the first day of a run, the day of a screen; None: no day to compare)."""
    code = strategy_scope(strategy)
    if code is None:
        return []
    name = (SCOPES.get(code) or LEGACY_SCOPES[code])[0]
    since = covers(held).get(code)
    if since is None:
        params = {"scope": name, "data": scope_label(held[0].scope)}
        return [Warning(code="scope_beyond_data", text=DATA_SCOPE_TEXTS["scope_beyond_data"](params), params=params)]
    if start is None or start >= since:
        return []
    before = next((layer for layer in held if layer.since < since), held[-1])
    params = {"scope": name, "data": scope_label(before.scope), "since": since.isoformat()}
    text = DATA_SCOPE_TEXTS["scope_beyond_data_before"](params)
    return [Warning(code="scope_beyond_data_before", text=text, params=params)]


def layers_out(held: list[Layer]) -> list[ScopeLayer]:
    """The ScopeLayers of layers, with their Chinese names."""
    return [
        ScopeLayer(**DataScope.of(layer.scope).model_dump(), label=scope_label(layer.scope), since=layer.since)
        for layer in held
    ]


def scope_status(store: Store, found: dict) -> ScopeStatus:
    """The ScopeStatus of a store version."""
    first = store.read_table("calendar", columns=["date"], manifest=found)["date"].min()
    held = layers(found, first)
    floor = members_floor(held[-1].scope)
    reached = floor is not None and first <= floor[1]
    return ScopeStatus(
        layers=layers_out(held),
        covers=[ScopeCover(code=code, since=since) for code, since in covers(held).items()],
        stock_count=store.read_table("securities", columns=["code"], manifest=found).height,
        first_date=first,
        backfill_floor=BackfillFloor(index=floor[0], name=SCOPES[floor[0]][0], date=floor[1]) if reached else None,
    )


def scope_size(scope: Scope, years: int) -> int:
    """The stocks a first build of the last `years` years of scope fetches, from measured counts and the list."""
    if scope.all:
        return ALL_STOCKS[years]
    return (MEMBER_STOCKS[scope.indexes][years] if scope.indexes else 0) + len(scope.stocks)


class Size(NamedTuple):
    """What an extension fetches: stocks, their histories (a stock in each segment it needs), the stocks new to the
    data (each a factor history) and the segments."""

    stocks: int
    histories: int
    new: int
    segments: int


def extend_size(store: Store, found: dict, added: Scope, years: int) -> Size:
    """The segments data_extend would fetch for added and the stocks each needs, as data.build.extend picks them, from
    the raw layer's stock_basic (else the stored securities) and the stored member spans."""
    calendar = store.read_table("calendar", columns=["date"], manifest=found)["date"]
    held, basic = layers(found, calendar.min()), _basic(store, found)
    spans = store.read_table("index_members", manifest=found)
    reached = reach(held, added)
    windows = segments(reached - dt.timedelta(days=1) if reached else calendar.max(), calendar.min(), years)
    fetched, histories = set(), 0
    for start, end in windows:
        base = at(held, calendar.filter(calendar >= start).min())
        stocks = set(needed(base | added, start, end, basic, spans)) - set(needed(base, start, end, basic, spans))
        fetched, histories = fetched | stocks, histories + len(stocks)
    codes = store.read_table("securities", columns=["code"], manifest=found)["code"]
    stored = {to_baostock(code) for code in codes if not code.endswith(".BJ")}
    return Size(len(fetched), histories, len(fetched - stored), len(windows))


def _basic(store: Store, found: dict) -> pl.DataFrame:
    """The raw layer's stock_basic, or one made from the stored SH/SZ securities (official dates, else the data's)."""
    path = BaoStockRaw.at(store.home).stock_basic
    if path.exists():
        return pl.read_parquet(path)
    securities = store.read_table("securities", manifest=found).filter(~pl.col("code").str.ends_with(".BJ"))
    return securities.select(
        code=pl.col("code").map_elements(to_baostock, return_dtype=pl.String),
        ipoDate=pl.coalesce("list_date", "first_date").cast(pl.String),
        outDate=pl.col("delist_date").cast(pl.String).fill_null(""),
        type=pl.lit("1"),
    )
