"""The first build and the history behind it (round 3, S2.9): an empty data root's first version from BaoStock (the
last N years of the stocks its scope picks (S2.11), with names, dates, adjustment factors, and the offered indexes'
members and bars), then earlier years in segments, each its own version.

Each stock's rows over the window come from one history request (measured in round 3 to be about 2.4 times as fast as
a cross-section per day). Every response goes to the raw layer before anything else happens, so a build or a segment
stopped half way carries on from the files on disk next time and makes the same version as one run straight through.
Nothing is committed until a segment's merge is complete; a cancel leaves no version.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from wequant.data.files import check_inside
from wequant.data.indexes import MEMBER_QUERIES, MEMBERS_SINCE, OFFERED, stored_as
from wequant.data.members import spans_from_lists
from wequant.data.merge import apply_merge, plan_merge
from wequant.data.merge.daily import read_history
from wequant.data.names import basic_rows, with_basic
from wequant.data.raw import BaoStockRaw
from wequant.data.schema import TABLES
from wequant.data.scope import Layer, Scope, as_meta, layers, needed, widen
from wequant.data.sources.baostock import FIRST_DAY, INDEX_FIELDS, BaoStock, from_baostock, parse_index, to_baostock
from wequant.data.store import Store, exclusive
from wequant.data.update import SHANGHAI, Step, UpdateCancelled, due_days, refresh
from wequant.data.update import _fetch as fetch

STAGES = ("build_calendar", "build_stocks", "build_factors", "build_indexes", "build_merge", "build_commit")
HISTORY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,isST"
OLDEST = 2000  # the history a backfill reaches back to
SEGMENT_YEARS = 3  # years of history one backfill version adds
FACTOR_SECONDS = 0.5  # a factor history, for the time left before the factor stage has measured its own


@dataclass
class Built:
    """What a build or a backfill segment did: built, exists (a store is there already), complete (nothing older to
    add) or not_published (BaoStock has no day of the window yet)."""

    status: str
    version: int | None = None
    first: dt.date | None = None
    last: dt.date | None = None
    days: int = 0
    requests: int = 0
    stocks: int = 0  # stocks with bars in the version
    floor: tuple[str, dt.date] | None = None  # complete because the scope's index has no earlier member list


Say = Callable[[Step], None]


def build(
    store: Store,
    source: BaoStock,
    years: int,
    now: dt.datetime,
    progress: Say | None = None,
    cancel: Callable[[], bool] | None = None,
    start: dt.date | None = None,
    *,
    scope: Scope,
) -> Built:
    """The first version of an empty data root: the last `years` years (or from start) of the stocks scope picks, up
    to the latest closed trading day; a second build finds the store there and makes no version."""
    if (found := store.manifest()) is not None:
        return Built("exists", found["version"])
    say, cancel = progress or (lambda step: None), cancel or (lambda: False)
    local = now.astimezone(SHANGHAI).date()
    start = start or local.replace(year=local.year - years) + dt.timedelta(days=1)
    return _segment(store, source, start, None, now, say, cancel, [Layer(scope, start)], first=True)


def backfill(
    store: Store,
    source: BaoStock,
    now: dt.datetime,
    progress: Say | None = None,
    cancel: Callable[[], bool] | None = None,
    oldest: int = OLDEST,
) -> Built:
    """One segment of history before the store's first day for its oldest layer's scope, SEGMENT_YEARS years at most,
    as one version; complete once no trading day is left from the start of `oldest`, or from the first member list of
    an index the scope names, to the store's first day."""
    say, cancel = progress or (lambda step: None), cancel or (lambda: False)
    found = store.manifest()
    if found is None:
        raise ValueError(f"{store.home} has no version to add history to")
    first = store.read_table("calendar", columns=["date"], manifest=found)["date"].min()
    held = layers(found, first)
    floor = members_floor(held[-1].scope)
    lower = max(dt.date(oldest, 1, 1), floor[1] if floor else dt.date.min)
    end = first - dt.timedelta(days=1)
    if end < lower:
        return Built("complete", found["version"], floor=floor if floor and floor[1] >= dt.date(oldest, 1, 1) else None)
    start = max(lower, dt.date(end.year - SEGMENT_YEARS + 1, 1, 1))
    return _segment(store, source, start, end, now, say, cancel, held, first=False)


def extend(
    store: Store,
    source: BaoStock,
    scope: Scope,
    now: dt.datetime,
    progress: Say | None = None,
    cancel: Callable[[], bool] | None = None,
    name: str = "",
) -> Built:
    """One segment of a wider scope (holding the widest layer's) as one version: the stocks it picks over the segment
    that have no bars there, newest segment first, SEGMENT_YEARS years at most; covered once the oldest layer holds
    scope. name goes into the version's reason (scope_extend)."""
    say, cancel = progress or (lambda step: None), cancel or (lambda: False)
    found = store.manifest()
    if found is None:
        raise ValueError(f"{store.home} has no version to extend")
    calendar = store.read_table("calendar", columns=["date"], manifest=found)["date"]
    held = layers(found, calendar.min())
    if scope <= held[-1].scope:
        return Built("covered", found["version"])
    end = held[0].since - dt.timedelta(days=1) if held[0].scope == scope else calendar.max()
    start = max(calendar.min(), dt.date(end.year - SEGMENT_YEARS + 1, 1, 1))
    days = calendar.filter(calendar.is_between(start, end)).to_list()
    say = _with_years(say, start, end)
    raw = BaoStockRaw.at(store.home)
    check_inside(store.home, [raw.root, store.home / "manifests"])
    raw.root.mkdir(parents=True, exist_ok=True)
    with exclusive(raw.root / ".lock"):
        say(Step(STAGES[0], 0.0, {}))
        basic = fetch(source, raw, raw.stock_basic, "query_stock_basic", {}, force=True)
        spans = store.read_table("index_members", manifest=found)
        whole = next(layer for layer in held if layer.since <= days[0])  # its stocks have every bar of the segment
        have = set(needed(whole.scope, start, end, basic, spans))
        stocks = [code for code in needed(scope, start, end, basic, spans) if code not in have]
        paths = _histories(source, raw, stocks, start, end, say, cancel)
        daily = read_history(paths, days) if paths else None
        params = {"name": name, "first_year": start.year, "last_year": end.year}
        meta = as_meta(widen(held, scope, days[0]))
        if daily is None or daily.is_empty():  # no stock of the scope traded then: the layer reaches back all the same
            _check(cancel)
            result = store.commit(meta, reason="scope_extend", reason_params=params | {"days": 0})
            return Built("built", result.version, days[0], days[-1], 0, source.requests, _stocks(store))
        added = _new_codes(store, daily)
        _factors(source, raw, added, now, say, cancel)
        say(Step(STAGES[4], 0.9, {}))
        plan = plan_merge(store, raw, days=days, factor_codes=added, daily=daily, codes=sorted(set(daily["code"])))
        if plan.unresolved.height:
            raise ValueError(f"{plan.unresolved.height} conflicts with stored rows; the store is unchanged")
        plan.replace["securities"] = with_basic(plan.replace["securities"], basic_rows(basic))
        plan.meta |= meta
        _check(cancel)
        say(Step(STAGES[5], 0.97, {}))
        result = apply_merge(store, plan, reason="scope_extend", params=params | {"days": len(days)})
    return Built("built", result.version, days[0], days[-1], len(days), source.requests, _stocks(store))


def join(
    store: Store,
    source: BaoStock,
    before: dt.date,
    now: dt.datetime,
    progress: Say | None = None,
    cancel: Callable[[], bool] | None = None,
) -> int:
    """After an update of the days after before: the members of the widest layer's indexes on the last day (and its
    listed stocks) that had no bars up to before, with their history from the layer's first day to before, as one
    version (members_joined); how many stocks it added."""
    say, cancel = progress or (lambda step: None), cancel or (lambda: False)
    found = store.manifest()
    calendar = store.read_table("calendar", columns=["date"], manifest=found)["date"]
    widest = layers(found, calendar.min())[0]
    if widest.scope.all:
        return 0
    spans = store.read_table("index_members", manifest=found)
    members = needed(widest.scope, calendar.max(), calendar.max(), pl.DataFrame(), spans)  # on the last day
    had = set(store.scan("bars", columns=["code"], end=before, manifest=found).collect()["code"])
    joiners = [code for code in members if from_baostock(code) not in had]
    days = calendar.filter(calendar.is_between(widest.since, before)).to_list()
    if not joiners or not days:
        return 0
    raw = BaoStockRaw.at(store.home)
    with exclusive(raw.root / ".lock"):
        paths = _histories(source, raw, joiners, days[0], days[-1], say, cancel)
        daily = read_history(paths, days) if paths else None
        if daily is None or daily.is_empty():
            return 0
        added = sorted(set(daily["code"]))
        plan = plan_merge(store, raw, days=days, factor_codes=_new_codes(store, daily), daily=daily, codes=added)
        if plan.unresolved.height:
            raise ValueError(f"{plan.unresolved.height} conflicts with stored rows; the store is unchanged")
        _check(cancel)
        apply_merge(store, plan, reason="members_joined", params={"count": len(added)})
    return len(added)


def members_floor(scope: Scope) -> tuple[str, dt.date] | None:
    """The index of scope whose member lists start latest, and that day: no history before it has a scope."""
    return max(((index, MEMBERS_SINCE[index]) for index in scope.indexes), key=lambda found: found[1], default=None)


def _segment(store, source, start, end, now, say, cancel, held, first) -> Built:
    """Fetch the trading days from start to end (None: the latest closed one) of the stocks the oldest of the held
    layers picks into the raw layer, then merge them; that layer then starts on the first day fetched."""
    if not first:  # a backfill's steps name its years (F-33)
        say = _with_years(say, start, end)
    raw = BaoStockRaw.at(store.home)
    check_inside(store.home, [raw.root, store.home / "manifests"])
    raw.root.mkdir(parents=True, exist_ok=True)
    with exclusive(raw.root / ".lock"):  # one BaoStock session: the update and the backfill tool hold it too
        say(Step(STAGES[0], 0.0, {}))
        trade_dates = refresh(source, raw, start, now)
        days = [d for d in due_days(trade_dates, start - dt.timedelta(days=1), now) if end is None or d <= end]
        if not days:  # a backfill below the first trading day there is
            return Built("not_published" if first else "complete", requests=source.requests)
        basic = fetch(source, raw, raw.stock_basic, "query_stock_basic", {}, force=True)
        bars, members = _indexes(source, raw, days, say, cancel)
        stocks = needed(held[-1].scope, days[0], days[-1], basic, members)
        paths = _histories(source, raw, stocks, days[0], days[-1], say, cancel)
        daily = read_history(paths, days) if paths else None
        fetched = sorted(set(daily["date"])) if daily is not None else []
        if not fetched:
            return Built("not_published", requests=source.requests)
        bars, members = bars.filter(pl.col("date").is_in(fetched)), _through(members, fetched[-1])
        codes = _new_codes(store, daily)
        _factors(source, raw, codes, now, say, cancel)
        say(Step(STAGES[4], 0.9, {}))
        plan = plan_merge(store, raw, days=fetched, factor_codes=codes, first=first, daily=daily)
        if plan.unresolved.height:
            raise ValueError(f"{plan.unresolved.height} conflicts with stored rows; the store is unchanged")
        plan.replace["securities"] = with_basic(plan.replace["securities"], basic_rows(basic))
        plan.upsert["index_bars"] = bars
        plan.upsert["index_members"] = members
        _check(cancel)
        say(Step(STAGES[5], 0.97, {}))
        reason, params = _reason(first, fetched)
        plan.meta |= as_meta([*held[:-1], Layer(held[-1].scope, fetched[0])])
        result = apply_merge(store, plan, reason=reason, params=params)
    return Built("built", result.version, fetched[0], fetched[-1], len(fetched), source.requests, _stocks(store))


def _with_years(say: Say, start: dt.date, end: dt.date) -> Say:
    """say with the segment's first and last year in every step's params."""
    years = {"first_year": start.year, "last_year": end.year}
    return lambda step: say(step._replace(params={**step.params, **years}))


def _stocks(store: Store) -> int:
    """Stocks with bars in the store's current version."""
    return store.read_table("securities", columns=["code"]).height


def _through(spans: pl.DataFrame, last: dt.date) -> pl.DataFrame:
    """Member spans cut at last, the last day with bars."""
    return spans.filter(pl.col("start_date") <= last).with_columns(end_date=pl.min_horizontal("end_date", pl.lit(last)))


def _histories(source, raw, stocks, first, last, say, cancel) -> list[Path]:
    """Each stock's history file over the window, fetching the ones not on disk; the files with rows."""
    window = {"start_date": first.isoformat(), "end_date": last.isoformat(), "frequency": "d", "adjustflag": "3"}
    paths, pace = [], Pace()
    for n, code in enumerate(stocks):
        _check(cancel)
        histories = pace.left(len(stocks) - n)
        left = None if histories is None else histories + len(stocks) * FACTOR_SECONDS
        params = {"code": from_baostock(code), "done": n, "total": len(stocks), "eta_seconds": left}
        say(Step(STAGES[1], 0.06 + 0.6 * n / len(stocks), params))
        path = raw.history(from_baostock(code), first, last)
        fetched = not path.exists()
        request = {"code": code, "fields": HISTORY_FIELDS, **window}
        frame = fetch(source, raw, path, "query_history_k_data_plus", request, empty=True)
        pace.count(fetched)
        if frame.height:
            paths.append(path)
    return paths


class Pace:
    """Seconds per item fetched (items already on disk cost nothing), for the time a stage has left."""

    def __init__(self) -> None:
        self.started, self.fetched = time.monotonic(), 0

    def count(self, fetched: bool) -> None:
        self.fetched += fetched

    def left(self, items: int) -> float | None:
        """Seconds the items left take at the pace so far; None before a first fetch."""
        if not self.fetched:
            return None
        return round(items * (time.monotonic() - self.started) / self.fetched)


def _new_codes(store: Store, daily: pl.DataFrame) -> list[str]:
    """The SH/SZ codes the rows have that the store has no factor history for."""
    found = store.manifest()
    stored = set() if found is None else set(store.read_table("adj_factor", columns=["code"], manifest=found)["code"])
    return sorted(set(daily["code"]) - stored)


def _factors(source, raw, codes, now, say, cancel) -> None:
    """Each code's whole factor history, for the codes without a factor file."""
    missing = [code for code in codes if not raw.adjust_factor(code).exists()]
    end = now.astimezone(SHANGHAI).date().isoformat()
    pace = Pace()
    for n, code in enumerate(missing):
        _check(cancel)
        left = pace.left(len(missing) - n) or (len(missing) - n) * FACTOR_SECONDS
        say(Step(STAGES[2], 0.66 + 0.22 * n / len(missing), {"done": n, "total": len(missing), "eta_seconds": left}))
        params = {"code": to_baostock(code), "start_date": FIRST_DAY, "end_date": end}
        fetch(source, raw, raw.adjust_factor(code), "query_adjust_factor", params, empty=True)
        pace.count(True)


def _indexes(source, raw, days, say, cancel) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The offered indexes' bars over the days, and their member spans from member lists of each month's first trading
    day and the last day."""
    first, last = days[0], days[-1]
    window = {"start_date": first.isoformat(), "end_date": last.isoformat(), "frequency": "d", "adjustflag": "3"}
    samples = sorted({min(d for d in days if (d.year, d.month) == (m.year, m.month)) for m in days} | {last})
    bars, spans = (
        [pl.DataFrame(schema=TABLES["index_bars"].schema)],
        [pl.DataFrame(schema=TABLES["index_members"].schema)],
    )
    for n, index in enumerate(OFFERED):
        _check(cancel)
        say(Step(STAGES[3], 0.02 + 0.04 * n / len(OFFERED), {}))
        params = {"code": to_baostock(index), "fields": INDEX_FIELDS, **window}
        response = fetch(source, raw, raw.index(index, first, last), "query_history_k_data_plus", params, empty=True)
        if response.height:
            bars.append(parse_index(response, f"baostock@{response['_baostock_version'].first()}"))
        lists = {}
        for day in samples:
            found = fetch(
                source, raw, raw.members(index, day), MEMBER_QUERIES[index], {"date": day.isoformat()}, empty=True
            )
            if found.height:
                listed = dt.date.fromisoformat(found["updateDate"].max())
                lists[min(listed, day)] = {from_baostock(code) for code in found["code"]}
        spans.append(spans_from_lists(stored_as(index), lists, days))
    return pl.concat(bars).filter(pl.col("date").is_in(days)), pl.concat(spans)


def _reason(first: bool, days: list[dt.date]) -> tuple[str, dict]:
    if first:
        return "first_build", {"days": len(days)}
    return "history_backfill", {"first_year": days[0].year, "last_year": days[-1].year, "days": len(days)}


def _check(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise UpdateCancelled("stopped; what was fetched stays in the raw layer and the store is unchanged")
