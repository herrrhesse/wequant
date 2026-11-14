"""The daily update (S2.5): BaoStock's day snapshots after the store's last trading day, fetched into the raw layer and
merged as one version, with names, official dates, and the bars and members of the offered indexes (CSI 300 and CSI
500, round 3) refreshed; other indexes' stored rows stay as they are.

Each day costs two requests (the snapshot and its adjustment events); the calendar, stock_basic and each offered index's
bars and member list cost one per update, and a code new to the store or without a factor file one more. A day BaoStock has not published
yet ends the fetch, and the days before it are merged. A conflict the merge cannot settle becomes a proposed
resolution in the data root's file and stops the update with the store unchanged until the user decides.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NamedTuple

import polars as pl

from wequant.data.files import check_inside
from wequant.data.indexes import MEMBER_QUERIES, OFFERED, stored_as
from wequant.data.members import member_rows
from wequant.data.merge import apply_merge, plan_merge
from wequant.data.merge.daily import read_daily, read_history
from wequant.data.merge.propose import propose
from wequant.data.merge.resolutions import add, default_path
from wequant.data.merge.resolutions import load as load_resolutions
from wequant.data.names import basic_rows, with_basic
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.data.schema import TABLES
from wequant.data.scope import layers
from wequant.data.sources.baostock import (
    FIRST_DAY,
    HISTORY_FIELDS,
    INDEX_FIELDS,
    BaoStock,
    from_baostock,
    parse_index,
    to_baostock,
)
from wequant.data.store import Store, exclusive

SHANGHAI = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")  # no daylight saving since 1991; Windows has no tz data
PUBLISHED = dt.time(18, 0)  # Shanghai time by which BaoStock has loaded a day's bars (about 17:30)
STAGES = ("update_calendar", "update_snapshot", "update_factors", "update_indexes", "update_merge", "update_commit")
SECONDS_PER_DAY = 60.0  # one day's cross-section of every stock (59.8 s, S2.11 probe of 2026-11-14)
SECONDS_PER_STOCK = 0.8  # one stock's days (0.3 to 1.5 s, the same probe)


class Step(NamedTuple):
    """Where an update is: a STAGES code, the share done and the stage's params."""

    stage: str
    fraction: float
    params: dict


class UpdateCancelled(Exception):
    """The update stopped at its cancel signal; the store is unchanged."""


class RawRejected(ValueError):
    """BaoStock answered with rows dated other than the day asked for; nothing is written."""


@dataclass
class Outcome:
    """What one update did: updated, up_to_date, not_published or conflicts."""

    status: str
    base_version: int
    version: int | None = None  # the new version, when updated
    days: list[dt.date] = field(default_factory=list)  # merged, or held back by conflicts
    waiting: dt.date | None = None  # the first day BaoStock has not published
    proposed: int = 0  # resolutions proposed for conflicts
    unresolved: int = 0
    issues: dict[str, int] = field(default_factory=dict)  # this run's data_issues rows by kind
    indexes: dict[str, int] = field(default_factory=dict)  # index rows added, by code
    members: dict[str, int] = field(default_factory=dict)  # member spans grown or started, by index
    requests: int = 0
    joined: int = 0  # new members whose history was added after the update (S2.11)


def update(
    store: Store,
    source: BaoStock,
    now: dt.datetime,
    progress: Callable[[Step], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Outcome:
    """Fetch and merge the trading days after the store's last one up to the latest closed day as of now."""
    raw, say, cancel = BaoStockRaw.at(store.home), progress or (lambda step: None), cancel or (lambda: False)
    base = store.manifest()
    if base is None:
        raise ValueError(f"{store.home} has no committed version to update")
    check_inside(store.home, [raw.root, *(store.home / table for table in base["tables"]), store.home / "manifests"])
    raw.root.mkdir(parents=True, exist_ok=True)
    with exclusive(raw.root / ".lock"):  # one BaoStock session: the backfill and refetch hold it too
        say(Step(STAGES[0], 0.0, {}))
        last = store.read_table("calendar", columns=["date"], manifest=base)["date"].max()
        due = due_days(refresh(source, raw, last, now), last, now)
        outcome = Outcome("up_to_date", base["version"])
        if due:
            outcome = _merge(store, source, raw, base, due, now, say, cancel)
    outcome.requests = source.requests
    return outcome


def due_days(trade_dates: pl.DataFrame, last: dt.date, now: dt.datetime) -> list[dt.date]:
    """Trading days after last up to the latest closed one: today only once PUBLISHED has passed in Shanghai."""
    local = now.astimezone(SHANGHAI)
    latest = local.date() if local.time() >= PUBLISHED else local.date() - dt.timedelta(days=1)
    days = trade_dates.filter(pl.col("is_trading_day") == "1")["calendar_date"].str.to_date()
    return sorted(d for d in days if last < d <= latest)


def refresh(source: BaoStock, raw: BaoStockRaw, last: dt.date, now: dt.datetime) -> pl.DataFrame:
    """BaoStock's trading calendar from the start of last's year to the end of next year, written into
    raw/baostock/meta/trade_dates.parquet over those days; earlier rows stay, each row naming the request it came from."""
    start, end = dt.date(last.year, 1, 1), dt.date(now.astimezone(SHANGHAI).year + 1, 12, 31)
    fresh = _response(source, "query_trade_dates", {"start_date": start.isoformat(), "end_date": end.isoformat()})
    if raw.trade_dates.exists():
        earlier = pl.read_parquet(raw.trade_dates).filter(pl.col("calendar_date") < start.isoformat())
        fresh = pl.concat([earlier, fresh], how="diagonal")
    raw.write(raw.trade_dates, fresh)
    return fresh


def _merge(store, source, raw, base, due, now, say, cancel) -> Outcome:
    """Fetch the due days (by stock for a small list, else by day), then plan, check and commit their merge."""
    stocks = _by_stock(store, base, due)
    if stocks is None:
        days, waiting = _fetch_days(source, raw, due, say, cancel)
        daily = read_daily(raw, days) if days else None
    else:
        days, waiting, daily = _fetch_stocks(source, raw, stocks, due, say, cancel)
    if not days:
        return Outcome("not_published", base["version"], waiting=waiting)
    basic = _fetch(source, raw, raw.stock_basic, "query_stock_basic", {}, force=True)
    members = _fetch_members(source, raw, store, base, days)
    kept = _in_scope(store, base, members, days[-1])
    listed = set(daily["code"])
    codes = [code for code in _factor_codes(store, base, raw, days, listed) if kept is None or code in kept]
    _fetch_factors(source, raw, codes, now, say, cancel)
    indexes = _fetch_indexes(source, raw, days, say)
    say(Step(STAGES[4], 0.8, {}))
    daily = daily if kept is None else daily.filter(pl.col("code").is_in(sorted(kept)))
    plan = plan_merge(store, raw, days=days, factor_codes=codes, daily=daily)
    if plan.unresolved.height:
        path = default_path(store.home)
        proposed = add(path, propose(store, raw, days, load_resolutions(path)))
        held = {"days": days, "waiting": waiting, "proposed": proposed, "unresolved": plan.unresolved.height}
        return Outcome("conflicts", base["version"], **held)
    plan.replace["securities"] = with_basic(plan.replace["securities"], basic_rows(basic))
    plan.upsert["index_bars"] = indexes
    plan.upsert["index_members"] = pl.concat(members.values())
    _check(cancel)
    say(Step(STAGES[5], 0.95, {}))
    result = apply_merge(store, plan, reason="daily_update")
    added = dict(indexes.group_by("code").len().sort("code").rows())
    issues = dict(plan.issues.group_by("kind").len().sort("kind").rows())
    spans = {index: rows.height for index, rows in members.items() if rows.height}
    held = {"issues": issues, "indexes": added, "members": spans}
    return Outcome("updated", base["version"], result.version, days, waiting, **held)


def _in_scope(store: Store, base: dict, members: dict[str, pl.DataFrame], last: dt.date) -> set[str] | None:
    """The stocks an update keeps for a data root whose widest layer is not every stock (S2.11): those with bars, the
    members of its indexes on the last new day, and its listed stocks; None: every stock."""
    first = store.read_table("calendar", columns=["date"], manifest=base)["date"].min()
    scope = layers(base, first)[0].scope
    if scope.all:
        return None
    held = set(store.read_table("securities", columns=["code"], manifest=base)["code"])
    current = {
        code
        for index in scope.indexes
        if index in members
        for code in members[index].filter(pl.col("end_date") == last)["code"]
    }
    return held | current | set(scope.stocks)


def _by_stock(store: Store, base: dict, due: list[dt.date]) -> list[str] | None:
    """The stocks of a data root whose widest layer is a list alone, short enough that fetching each stock's days costs
    less than each day's cross-section (S2.11); None otherwise."""
    first = store.read_table("calendar", columns=["date"], manifest=base)["date"].min()
    scope = layers(base, first)[0].scope
    if scope.all or scope.indexes or len(scope.stocks) * SECONDS_PER_STOCK >= len(due) * SECONDS_PER_DAY:
        return None
    return list(scope.stocks)


def _fetch_stocks(source, raw, stocks, due, say, cancel) -> tuple[list[dt.date], dt.date | None, pl.DataFrame]:
    """Each stock's rows over the due days and each such day's adjustment events: (the days up to the last with a
    row, the first due day after it, the rows)."""
    window = {"start_date": due[0].isoformat(), "end_date": due[-1].isoformat(), "frequency": "d", "adjustflag": "3"}
    paths = []
    for n, code in enumerate(stocks):
        _check(cancel)
        say(
            Step(
                STAGES[1], 0.05 + 0.55 * n / len(stocks), {"day": due[-1].isoformat(), "done": n, "total": len(stocks)}
            )
        )
        paths.append(raw.history(code, due[0], due[-1]))
        params = {"code": to_baostock(code), "fields": HISTORY_FIELDS, **window}
        _fetch(source, raw, paths[-1], "query_history_k_data_plus", params, empty=True)
    daily = read_history(paths, due)
    last = daily["date"].max()
    days = [day for day in due if last is not None and day <= last]
    for day in days:
        _fetch(
            source,
            raw,
            raw.daily_factor(day),
            "query_daily_adjust_factor",
            {"date": day.isoformat()},
            empty=True,
            day=day,
        )
    return days, next((day for day in due if day not in days), None), daily.filter(pl.col("date").is_in(days))


def _fetch_days(source, raw, due, say, cancel) -> tuple[list[dt.date], dt.date | None]:
    """(days whose snapshot and events are on disk, in order; the first day BaoStock has not published)."""
    days = []
    for n, day in enumerate(due):
        _check(cancel)
        say(Step(STAGES[1], 0.05 + 0.55 * n / len(due), {"day": day.isoformat(), "done": n, "total": len(due)}))
        params = {"date": day.isoformat()}
        if _fetch(source, raw, raw.daily(day), "query_daily_history_k_AStock", params, day=day) is None:
            return days, day
        _fetch(source, raw, raw.daily_factor(day), "query_daily_adjust_factor", params, empty=True, day=day)
        days.append(day)
    return days, None


def _factor_codes(store, base, raw, days, listed: set[str]) -> list[str]:
    """Stocks whose factors the merge rebuilds: those with an event on the days, and those of the days' rows (listed)
    new to the store."""
    stored = set(store.read_table("securities", columns=["code"], manifest=base)["code"])
    events = {from_baostock(code) for day in days for code in _codes(raw.daily_factor(day))}
    return sorted((events & (listed | stored)) | (listed - stored))


def _fetch_factors(source, raw, codes, now, say, cancel) -> None:
    """Each code's factor history, for the codes without a factor file."""
    missing = [code for code in codes if not raw.adjust_factor(code).exists()]
    end = now.astimezone(SHANGHAI).date().isoformat()
    for n, code in enumerate(missing):
        _check(cancel)
        say(Step(STAGES[2], 0.6 + 0.1 * n / len(missing), {"done": n, "total": len(missing)}))
        params = {"code": to_baostock(code), "start_date": FIRST_DAY, "end_date": end}
        _fetch(source, raw, raw.adjust_factor(code), "query_adjust_factor", params, empty=True)


def _fetch_indexes(source, raw, days, say) -> pl.DataFrame:
    """index_bars rows of the days for each offered index."""
    say(Step(STAGES[3], 0.7, {}))
    codes = OFFERED
    window = {"start_date": days[0].isoformat(), "end_date": days[-1].isoformat(), "frequency": "d", "adjustflag": "3"}
    frames = [pl.DataFrame(schema=TABLES["index_bars"].schema)]
    for code in codes:
        params = {"code": to_baostock(code), "fields": INDEX_FIELDS, **window}
        path = raw.index(code, days[0], days[-1])
        response = _fetch(source, raw, path, "query_history_k_data_plus", params, empty=True)
        if response.height:
            source_name = f"baostock@{response['_baostock_version'].first()}"
            frames.append(parse_index(response, source_name).filter(pl.col("date").is_in(days)))
    return pl.concat(frames)


def _fetch_members(source, raw, store, base, days) -> dict[str, pl.DataFrame]:
    """index_members rows to upsert for each offered index, from its member list as of the last day; an index whose
    list comes back empty keeps its spans as they are."""
    spans = store.read_table("index_members", manifest=base)
    before = store.read_table("calendar", columns=["date"], manifest=base)["date"].max()
    found = {}
    for index in OFFERED:
        params = {"date": days[-1].isoformat()}
        listed = _fetch(source, raw, raw.members(index, days[-1]), MEMBER_QUERIES[index], params, empty=True)
        if listed.height:
            day = dt.date.fromisoformat(listed["updateDate"].max())
            codes = [from_baostock(code) for code in listed["code"]]
            found[index] = member_rows(spans, stored_as(index), codes, day, before, days)
    return found or {"": pl.DataFrame(schema=TABLES["index_members"].schema)}


def _fetch(source, raw, path, method, params, force=False, empty=False, day=None) -> pl.DataFrame | None:
    """The response of one query, written to path; a file already there is read instead unless force. None for an
    empty answer unless empty; RawRejected for rows dated other than day."""
    if path.exists() and not force:
        return pl.read_parquet(path)
    frame = _response(source, method, params)
    if not frame.height and not empty:
        return None
    dated = next((c for c in ("date", "dividOperateDate") if c in frame.columns), None)
    if day is not None and dated and (frame[dated] != day.isoformat()).any():
        raise RawRejected(f"{method} for {day}: rows dated other days")
    raw.write(path, frame)
    return frame


def _response(source: BaoStock, method: str, params: dict) -> pl.DataFrame:
    """One query's answer as the raw layer keeps it: string columns plus the request, time and BaoStock version."""
    fields, rows = source.query(method, **params)
    frame = pl.DataFrame(rows, schema={f: pl.String for f in fields}, orient="row")
    request = f"{method}({', '.join(f'{key}={value}' for key, value in params.items())})"
    return raw_frame(frame, request, _baostock_version())


def _codes(path: Path) -> list[str]:
    frame = pl.read_parquet(path)
    return frame["code"].to_list() if "code" in frame.columns else []


def _check(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise UpdateCancelled("update cancelled; the store is unchanged")


def _baostock_version() -> str:
    try:
        return version("baostock")
    except PackageNotFoundError:
        return "unknown"
