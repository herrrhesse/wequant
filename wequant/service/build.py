"""data build | build-estimate | backfill | extend | extend-estimate (round 3, S2.9; S2.11): the first version of an
empty data root from BaoStock for a scope, how long it will take, the earlier years and a wider scope in the
background. Builds, backfills and extensions are background jobs under wequant serve (kinds data_build, data_backfill
and data_extend), one BaoStock session at a time with the daily update."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from wequant.data.build import OLDEST, SEGMENT_YEARS, Built, Say, backfill, build, extend
from wequant.data.raw import BaoStockRaw
from wequant.data.scope import Scope
from wequant.data.sources.baostock import BaoStock
from wequant.data.update import Step
from wequant.service.base import Context, Model, manifest, name_of
from wequant.service.errors import Exit, ServiceError
from wequant.service.pools import PoolSaveRequest, pools_save
from wequant.service.scopes import (
    BackfillFloor,
    DataScope,
    ScopeLayer,
    Size,
    default_scope,
    extend_size,
    held_layers,
    layers_out,
    scope_label,
    scope_size,
)
from wequant.service.sessions import baostock
from wequant.service.specs import Name
from wequant.service.storage import tidy

TRADING_DAYS = 243  # a year's trading days, for an estimate before the calendar is known
STOCKS = 5_566  # SH/SZ stocks in BaoStock's stock_basic, delisted ones included (2026-11-14)
SECONDS_PER_HISTORY = {1: 2.0, 3: 3.9, 5: 5.8}  # one stock's history over the window (measured 2026-11-14 for 3 years)
SECONDS_PER_FACTOR = 0.51  # one stock's whole factor history (measured 2026-11-14)
SECONDS_INDEXES = 180.0  # the offered indexes' bars and monthly member lists
SECONDS_PER_SEGMENT = 2.0  # an extension segment's stock_basic and commit, with or without stocks to fetch
RECONNECTS = 5  # new sessions a build or a backfill segment starts after BaoStock fails, before it gives up
PAUSE = 60.0  # seconds before each new session (BaoStock's outages of 2026-11-14 lasted minutes)
STAGES = ("build_reconnect", "backfill_floor")  # waiting to start a new session; history stops at a member list


class BuildEstimateRequest(Model):
    """How long a first build of the last `years` years of a scope will take."""

    years: Literal[1, 3, 5] = 3
    scope: DataScope = Field(default_factory=default_scope)


class BuildEstimate(Model):
    """A first build's size and expected time, from measured BaoStock speeds; BaoStock's own speed varies."""

    years: int
    scope: DataScope
    stocks: int
    """Stocks to fetch: an index's from member counts measured over the last 1, 3 and 5 years, a list's its codes."""
    days: int
    """Trading days, from BaoStock's calendar when the raw layer has it, else 243 a year."""
    requests: int
    seconds: int
    """Expected time; the frontend shows it as 预计约 X 小时."""
    already: int
    """Stocks whose history files the raw layer holds from an earlier, stopped build: they cost nothing again."""


class BuildRequest(Model):
    """A first build of an empty data root from BaoStock: the last `years` years of a scope."""

    years: Literal[1, 3, 5] = 3
    scope: DataScope = Field(default_factory=default_scope)
    pool_name: Name | None = None
    """The stock pool a scope's stocks are saved as once built (default 首次建库名单); unused without stocks."""


class BuildResult(Model):
    """What a build or one backfill segment did."""

    status: Literal["built", "exists", "complete", "not_published"]
    """built: a new version; exists: the data root has data already (no new version); complete: no older history
    left; not_published: BaoStock has no day of the window yet."""
    version: int | None
    version_name: str | None
    first: dt.date | None
    last: dt.date | None
    days: int
    requests: int
    seconds: float
    stocks: int = 0
    """Stocks with bars in the version made; 0 without one."""
    pool_id: str | None = None
    """The stock pool a build saved its scope's stocks as; null otherwise."""


class BackfillRequest(Model):
    """The history before the data root's first day, back to the start of `oldest`, one version per segment."""

    oldest: int = Field(OLDEST, ge=1990)


class BackfillResult(Model):
    """The segments added, newest first, until no history was left."""

    segments: list[BuildResult]
    status: Literal["complete"]
    floor: BackfillFloor | None = None
    """Set when the history stops at the first member list of an index of the oldest layer (S2.11)."""


class ExtendEstimateRequest(Model):
    """How long adding scope to the data's widest layer will take."""

    scope: DataScope
    """What to add: an index, every stock or a list."""


class ExtendEstimate(Model):
    """An extension's size and expected time; sends no request."""

    scope: DataScope
    """The widest layer's scope once extended."""
    covered: bool
    """Nothing to fetch: the data holds it already from its first day, or holds every stock it picks."""
    stocks: int
    """Stocks to fetch: those new to the data and those it holds only from a later day."""
    requests: int
    seconds: int
    since: dt.date
    """The day the added stocks reach back to: the data's first day."""


class ExtendRequest(Model):
    """Add scope to the data's widest layer, newest years first, SEGMENT_YEARS years a version, back to its first day;
    the same request again carries on a stopped extension."""

    scope: DataScope
    pool_name: Name | None = None
    """The stock pool the added stocks are saved as (default 扩大范围名单); unused without stocks."""


class ExtendResult(Model):
    """The segments added, newest first, and the layers after them."""

    status: Literal["built", "covered"]
    """built: at least one segment; covered: nothing to add (no version)."""
    segments: list[BuildResult]
    layers: list[ScopeLayer]
    added: int
    """Stocks with bars now that had none before."""
    pool_id: str | None


def data_build_estimate(request: BuildEstimateRequest, context: Context = Context()) -> BuildEstimate:
    """A first build's stocks, trading days, requests and expected seconds for its scope; sends no request."""
    history = BaoStockRaw.at(context.store().home).root / "history"
    already = sum(1 for _ in history.glob("*/*.parquet")) if history.is_dir() else 0
    stocks = scope_size(request.scope.scope(), request.years)
    todo = max(stocks - already, 0)
    seconds = todo * SECONDS_PER_HISTORY[request.years] + stocks * SECONDS_PER_FACTOR + SECONDS_INDEXES
    return BuildEstimate(
        years=request.years,
        scope=request.scope,
        stocks=stocks,
        days=request.years * TRADING_DAYS,
        requests=todo + stocks + 2 * 12 * request.years + 10,
        seconds=round(seconds),
        already=min(already, stocks),
    )


def data_build(request: BuildRequest, context: Context = Context()) -> BuildResult:
    """Build the last `years` years from BaoStock into an empty data root as its first version; a stopped build
    carries on from what it fetched, and a cancel leaves no version."""
    started, store = time.monotonic(), context.store()
    if (found := store.manifest()) is not None:  # no BaoStock session for a data root that has data
        return _result(Built("exists", found["version"]), started, context)
    scope = request.scope.scope()
    work = lambda source, say: build(store, source, request.years, _now(context), say, context.cancel, scope=scope)  # noqa: E731
    built = _session(work, context)
    if built.version is None:
        return _result(built, started, context)
    tidy(context)
    pool = save_pool(context, request.pool_name or "首次建库名单", scope.stocks)
    return _result(built, started, context).model_copy(update={"pool_id": pool})


def data_backfill(request: BackfillRequest, context: Context = Context()) -> BackfillResult:
    """Add the earlier years segment by segment, each a version, until the start of `oldest`; a cancel keeps the
    segments already added and leaves no half segment."""
    store, segments = context.store(), []
    manifest(store)  # NOT_FOUND no_store: history goes behind a first version
    while True:
        started = time.monotonic()
        work = lambda source, say: backfill(store, source, _now(context), say, context.cancel, request.oldest)  # noqa: E731
        built = _session(work, context)
        if built.status != "built":
            return BackfillResult(segments=segments, status="complete", floor=_floor(built, context))
        tidy(context)
        segments.append(_result(built, started, context))


def data_extend_estimate(request: ExtendEstimateRequest, context: Context = Context()) -> ExtendEstimate:
    """How many stocks adding scope brings in and how long fetching them back to the data's first day takes; sends no
    request."""
    store = context.store()
    found = manifest(store)
    held, added = held_layers(store, found), request.scope.scope()
    widest, first = DataScope.of(held[0].scope | added), held[-1].since
    size = _size(store, found, added)
    if size is None:
        return ExtendEstimate(scope=widest, covered=True, stocks=0, requests=0, seconds=0, since=first)
    seconds = (
        size.histories * SECONDS_PER_HISTORY[SEGMENT_YEARS]
        + size.new * SECONDS_PER_FACTOR
        + size.segments * SECONDS_PER_SEGMENT
    )
    requests = size.histories + size.new + size.segments
    return ExtendEstimate(
        scope=widest, covered=False, stocks=size.stocks, requests=requests, seconds=round(seconds), since=first
    )


def data_extend(request: ExtendRequest, context: Context = Context()) -> ExtendResult:
    """Add scope's stocks segment by segment, newest first, each a version, until the data holds them from its first
    day; a cancel keeps the segments added and leaves no half segment."""
    store, segments = context.store(), []
    found = manifest(store)
    before = found["tables"]["securities"]
    added, name = request.scope.scope(), scope_label(request.scope.scope())
    named = lambda say: lambda step: say(step._replace(params={**step.params, "name": name}))  # noqa: E731
    while _size(store, manifest(store), added) is not None:
        started = time.monotonic()
        work = lambda source, say: extend(store, source, added, _now(context), named(say), context.cancel, name)  # noqa: E731
        built = _session(work, context)
        if built.status != "built":
            break
        tidy(context)
        segments.append(_result(built, started, context))
    after = manifest(store)
    pool = save_pool(context, request.pool_name or "扩大范围名单", request.scope.stocks) if segments else None
    return ExtendResult(
        status="built" if segments else "covered",
        segments=segments,
        layers=layers_out(held_layers(store, after)),
        added=_rows(after["tables"]["securities"]) - _rows(before),
        pool_id=pool,
    )


def _size(store, found: dict, added: Scope) -> Size | None:
    """What extending by added fetches; None when the data holds it from its first day or nothing is left to fetch."""
    held = held_layers(store, found)
    if added <= held[-1].scope:
        return None
    size = extend_size(store, found, added, SEGMENT_YEARS)
    return size if size.stocks else None


def save_pool(context: Context, name: str, codes: tuple[str, ...]) -> str | None:
    """The id of a new stock pool of codes; None without codes."""
    return pools_save(PoolSaveRequest(name=name, codes=list(codes)), context).id if codes else None


def _rows(table: dict) -> int:
    return sum(f["rows"] for f in table["files"])


def _floor(built: Built, context: Context) -> BackfillFloor | None:
    """The floor a backfill stopped at, said as its last step."""
    if built.floor is None:
        return None
    index, day = built.floor
    floor = BackfillFloor(index=index, name=scope_label(Scope(indexes=(index,))), date=day)
    if context.progress:
        context.progress(Step(STAGES[1], 1.0, floor.model_dump(mode="json")))
    return floor


def _session(work: Callable[[BaoStock, Say], Built], context: Context) -> Built:
    """work in a BaoStock session, started again (from the files fetched so far) while BaoStock fails, RECONNECTS
    times at most, PAUSE seconds apart; the last failure raises. The share done never goes back: the wait and a new
    session's first steps, over files already fetched, report the share reached before."""
    reached, cancel = [0.0], context.cancel or (lambda: False)

    def say(step: Step) -> None:
        reached[0] = max(reached[0], step.fraction)
        if context.progress:
            context.progress(step._replace(fraction=reached[0]))

    for attempt in range(RECONNECTS + 1):
        try:
            with baostock() as source:
                return work(source, say)
        except ServiceError as error:
            if error.exit != Exit.UNREACHABLE or attempt == RECONNECTS:
                raise
            say(Step(STAGES[0], 0.0, {"attempt": attempt + 1, "of": RECONNECTS, "seconds": round(PAUSE)}))
            _pause(PAUSE, cancel)
    raise AssertionError("unreachable")


def _pause(seconds: float, cancel: Callable[[], bool]) -> None:
    """Wait, looking at cancel every second."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel():
            raise ServiceError(Exit.INTERRUPTED, "stopped while waiting to reach BaoStock; the store is unchanged")
        time.sleep(min(1.0, max(end - time.monotonic(), 0)))


def _result(built: Built, started: float, context: Context) -> BuildResult:
    name = name_of(context.store(), built.version) if built.version is not None else None
    return BuildResult(
        stocks=built.stocks,
        status=built.status,
        version=built.version,
        version_name=name,
        first=built.first,
        last=built.last,
        days=built.days,
        requests=built.requests,
        seconds=round(time.monotonic() - started, 1),
    )


def _now(context: Context) -> dt.datetime:
    clock: Callable[[], str] | None = context.clock
    return dt.datetime.fromisoformat(clock()) if clock else dt.datetime.now(dt.UTC)
