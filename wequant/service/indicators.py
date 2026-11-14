"""expr indicators (S3.8, F-07): every indicator a condition or a factor can use, in Chinese, with the days the data has
for it: the panel fields of wequant.expr's vocabulary, the planned ones, and the common indicators built from them."""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

import numpy as np

from wequant.data.indexes import LEGACY, stored_as
from wequant.data.store import Store
from wequant.expr import FAMILIES, FIELDS, PLANNED, from_form
from wequant.expr.lookback import lookback
from wequant.service.base import Context, DataRequest, Model, named, read_version

Unit = Literal["yuan", "shares", "fraction", "ratio", "trading_days", "bool"]
Category = Literal[
    "price", "adjusted_price", "volume", "momentum", "volatility", "trend", "limit", "status", "classification",
    "membership", "listing",
]  # fmt: skip
RET = lambda n: ["SUB", ["DIV", "adj_close", ["REF", "adj_close", n]], 1]  # noqa: E731

FIELD_TEXT: dict[str, tuple[str, Unit, Category, str]] = {  # name -> (Chinese name, unit, category, sentence)
    "open": ("开盘价", "yuan", "price", "当天的开盘价，不复权"),
    "high": ("最高价", "yuan", "price", "当天的最高价，不复权"),
    "low": ("最低价", "yuan", "price", "当天的最低价，不复权"),
    "close": ("收盘价", "yuan", "price", "当天的收盘价，不复权"),
    "preclose": ("前收盘价", "yuan", "price", "交易所公布的前收盘价，用来算涨跌幅和涨跌停价"),
    "volume": ("成交量", "shares", "volume", "当天成交的股数"),
    "amount": ("成交额", "yuan", "volume", "当天成交的金额；个别日子可能没有这项数据"),
    "adj_open": ("后复权开盘价", "yuan", "adjusted_price", "按后复权调整过的开盘价，跨过除权日也能直接比较"),
    "adj_high": ("后复权最高价", "yuan", "adjusted_price", "按后复权调整过的最高价"),
    "adj_low": ("后复权最低价", "yuan", "adjusted_price", "按后复权调整过的最低价"),
    "adj_close": ("后复权收盘价", "yuan", "adjusted_price", "按后复权调整过的收盘价，算涨跌幅时用它"),
    "adj_preclose": ("后复权前收盘价", "yuan", "adjusted_price", "按后复权调整过的前收盘价"),
    "factor": ("复权因子", "ratio", "adjusted_price", "后复权因子：后复权价等于原始价乘以它"),
    "limit_up_price": ("涨停价", "yuan", "limit", "按当天的涨跌幅规则算出的涨停价"),
    "limit_down_price": ("跌停价", "yuan", "limit", "按当天的涨跌幅规则算出的跌停价"),
    "limit_up": ("收盘涨停", "bool", "limit", "收盘价等于涨停价"),
    "limit_down": ("收盘跌停", "bool", "limit", "收盘价等于跌停价"),
    "limit_locked": ("一字板", "bool", "limit", "全天最高价等于最低价，并且收在涨停或跌停"),
    "limit_known": ("涨跌停已知", "bool", "limit", "当天的涨跌幅规则是已知的；不知道时涨停、跌停都读作否"),
    "st": ("ST", "bool", "status", "当天被实施风险警示（ST）"),
    "st_known": ("ST 状态已知", "bool", "status", "当天是不是 ST 有记录；没有记录时 ST 读作否"),
    "list_days": ("上市交易日数", "trading_days", "listing", "从上市第一天算起已经交易的天数，当天也算"),
    "board@main": ("主板", "bool", "classification", "沪深主板的股票"),
    "board@chinext": ("创业板", "bool", "classification", "创业板的股票"),
    "board@star": ("科创板", "bool", "classification", "科创板的股票"),
    "board@bse": ("北交所板块", "bool", "classification", "北京证券交易所的股票（暂时没有数据）"),
    "exchange@SH": ("上交所", "bool", "classification", "在上海证券交易所上市"),
    "exchange@SZ": ("深交所", "bool", "classification", "在深圳证券交易所上市"),
    "exchange@BJ": ("北交所", "bool", "classification", "在北京证券交易所上市（暂时没有数据）"),
    "member@000300.SH": ("沪深300成分", "bool", "membership", "当天是沪深300指数的成分股"),
    "member@000905.SH": ("中证500成分", "bool", "membership", "当天是中证500指数的成分股"),
    "member@000906.SH": ("中证800成分", "bool", "membership", "当天是中证800指数的成分股；旧选项，不再更新"),
    "member@000852.SH": ("中证1000成分", "bool", "membership", "当天是中证1000指数的成分股；旧选项，不再更新"),
    "member@000985.SH": ("中证全指成分", "bool", "membership", "当天是中证全指的成分股；旧选项，不再更新"),
    "turn": ("换手率", "fraction", "volume", "当天成交量占流通股本的比例"),
}
NEEDS_MERGE = frozenset(("st", "st_known", "turn"))  # empty until the BaoStock merge (S2.4b) fills daily_status
DERIVED: dict[str, tuple[str, Unit, Category, str, list]] = {  # id -> (Chinese name, unit, category, sentence, form)
    **{
        f"change_{n}": (f"{n} 日涨跌幅", "fraction", "momentum", f"后复权收盘价相对 {n} 个交易日前的涨跌", RET(n))
        for n in (5, 20, 60, 120)
    },
    **{
        f"volatility_{n}": (
            f"{n} 日波动率",
            "fraction",
            "volatility",
            f"最近 {n} 个交易日日涨跌幅的标准差",
            ["STD", RET(1), n],
        )
        for n in (20, 60)
    },
    **{
        f"avg_amount_{n}": (
            f"{n} 日平均成交额",
            "yuan",
            "volume",
            f"最近 {n} 个交易日成交额的平均值",
            ["MA", "amount", n],
        )
        for n in (20, 60)
    },
    **{
        f"vs_ma_{n}": (
            f"相对 {n} 日均线",
            "fraction",
            "trend",
            f"后复权收盘价高出 {n} 日均线的比例，低于均线时为负",
            ["SUB", ["DIV", "adj_close", ["MA", "adj_close", n]], 1],
        )
        for n in (20, 60, 120)
    },
    **{
        f"from_high_{n}": (
            f"距 {n} 日最高",
            "fraction",
            "trend",
            f"后复权收盘价比最近 {n} 个交易日的最高价低多少，创新高时为 0",
            ["SUB", ["DIV", "adj_close", ["HHV", "adj_high", n]], 1],
        )
        for n in (60, 250)
    },
}


class IndicatorsRequest(DataRequest):
    """Every indicator, with the days the store version has for it."""


class Indicator(Model):
    """One indicator a condition or a factor can use."""

    id: str
    """A field name (close, board@main, member@000300.SH) or a common indicator's id (change_20)."""
    form: str | list
    """Its expression form, to write into a condition or a factor; a field's is its name."""
    label: str
    """Chinese name."""
    unit: Unit
    """yuan, shares, fraction (0.05 is 5%), ratio, trading_days or bool."""
    category: Category
    """labels.indicator_categories gives the Chinese."""
    help: str
    """One Chinese sentence."""
    kind: Literal["num", "bool"]
    available: bool
    """False: a planned field the expressions cannot read yet; see reason."""
    reason: str | None
    needs_merge: bool
    """Empty until the BaoStock merge (S2.4b) fills its table, e.g. ST and the turnover rate."""
    legacy: bool
    """An older index's membership: still read, no longer updated."""
    first: dt.date | None
    """The first day the data has a value for it; null: none (or no data)."""
    last: dt.date | None


class Indicators(Model):
    """The indicators: fields first, then the common indicators built from them."""

    data_version: int | None
    """The store version the days are read from; null: no data."""
    data_version_name: str | None
    rows: list[Indicator]


def expr_indicators(request: IndicatorsRequest, context: Context = Context()) -> Indicators:
    """Every indicator a condition or a factor can use, with the days the data has for it."""
    store = context.store()
    found = store.manifest(read_version(context, request.data_version))
    spans = _spans(store, found)
    fields = [_field(name, spans) for name in FIELD_TEXT]
    derived = [_derived(id, spans) for id in DERIVED]
    versions = named(found) if found else {"data_version": None, "data_version_name": None}
    return Indicators(**versions, rows=fields + derived)


def _field(name: str, spans: dict) -> Indicator:
    label, unit, category, help = FIELD_TEXT[name]
    family, _, param = name.partition("@")
    available = name not in PLANNED
    kind = (FIELDS[name].kind if name in FIELDS else FAMILIES[family].kind) if available else "num"
    first, last = _days(name, spans) if available else (None, None)
    return Indicator(
        id=name,
        form=name,
        label=label,
        unit=unit,
        category=category,
        help=help,
        kind=kind,
        available=available,
        reason=PLANNED.get(name),
        needs_merge=name in NEEDS_MERGE,
        legacy=family == "member" and param in LEGACY,
        first=first,
        last=last,
    )


def _derived(id: str, spans: dict) -> Indicator:
    label, unit, category, help, form = DERIVED[id]
    expr = from_form(form)
    first, last = _shifted(spans, lookback(expr))
    return Indicator(
        id=id,
        form=json.loads(expr.key),
        label=label,
        unit=unit,
        category=category,
        help=help,
        kind=expr.kind,
        available=True,
        reason=None,
        needs_merge=False,
        legacy=False,
        first=first,
        last=last,
    )


def _spans(store: Store, found: dict | None) -> dict:
    """(first, last) day of the bars, the daily_status rows and each stored index's members, and the calendar."""
    if found is None:
        return {}
    span = lambda frame, a="date", b="date": (frame[a].min(), frame[b].max()) if frame.height else (None, None)  # noqa: E731
    members = store.read_table("index_members", manifest=found)
    by_index = {code: span(rows, "start_date", "end_date") for (code,), rows in members.group_by("index_code")}
    return {
        "bars": span(store.scan("bars", columns=["date"], manifest=found).select("date").collect()),
        "daily_status": span(store.scan("daily_status", columns=["date"], manifest=found).collect()),
        "members": by_index,
        "calendar": store.read_table("calendar", columns=["date"], manifest=found)["date"].sort().to_numpy(),
    }


def _days(name: str, spans: dict) -> tuple[dt.date | None, dt.date | None]:
    if not spans:
        return None, None
    family, _, param = name.partition("@")
    if name in ("st", "st_known"):
        return spans["daily_status"]
    if family == "member":
        return spans["members"].get(stored_as(param), (None, None))
    return spans["bars"]


def _shifted(spans: dict, reach: int | None) -> tuple[dt.date | None, dt.date | None]:
    """The bars' span, its start moved on by reach trading days (an indicator's first day with a value)."""
    first, last = spans.get("bars", (None, None))
    if first is None or not reach:
        return first, last
    calendar = spans["calendar"]
    at = int(np.searchsorted(calendar, np.datetime64(first, "D"))) + reach
    return (calendar[at].astype(dt.date) if at < len(calendar) else None), last
