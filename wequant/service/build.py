"""data build | build-estimate | backfill (round 3, S2.9): the first version of an empty data root from BaoStock, how
long it will take, and the earlier years in the background. Each is a background job under wequant serve (kinds
data_build and data_backfill), one BaoStock session at a time with the daily update."""

from __future__ import annotations

import datetime as dt
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal

from pydantic import Field

from wequant.data.build import OLDEST, Built, Say, backfill, build
from wequant.data.merge import StalePlan
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import BaoStock, BaoStockError
from wequant.data.store import StoreLocked
from wequant.data.update import Step, UpdateCancelled
from wequant.service.base import Context, ErrorDetail, Model, guarded, manifest, name_of
from wequant.service.errors import Exit, ServiceError
from wequant.service.storage import tidy

TRADING_DAYS = 243  # a year's trading days, for an estimate before the calendar is known
STOCKS = 5_566  # SH/SZ stocks in BaoStock's stock_basic, delisted ones included (2026-11-14)
SECONDS_PER_HISTORY = {1: 2.0, 3: 3.9, 5: 5.8}  # one stock's history over the window (measured 2026-11-14 for 3 years)
SECONDS_PER_FACTOR = 0.51  # one stock's whole factor history (measured 2026-11-14)
SECONDS_INDEXES = 180.0  # the offered indexes' bars and monthly member lists
RECONNECTS = 5  # new sessions a build or a backfill segment starts after BaoStock fails, before it gives up
PAUSE = 60.0  # seconds before each new session (BaoStock's outages of 2026-11-14 lasted minutes)
STAGES = ("build_reconnect",)  # waiting to start a new session


class BuildEstimateRequest(Model):
    """How long a first build of the last `years` years will take."""

    years: Literal[1, 3, 5] = 3


class BuildEstimate(Model):
    """A first build's size and expected time, from measured BaoStock speeds; BaoStock's own speed varies."""

    years: int
    days: int
    """Trading days, from BaoStock's calendar when the raw layer has it, else 243 a year."""
    requests: int
    seconds: int
    """Expected time; the frontend shows it as 预计约 X 小时."""
    already: int
    """Stocks whose history files the raw layer holds from an earlier, stopped build: they cost nothing again."""


class BuildRequest(Model):
    """A first build of an empty data root from BaoStock."""

    years: Literal[1, 3, 5] = 3


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


class BackfillRequest(Model):
    """The history before the data root's first day, back to the start of `oldest`, one version per segment."""

    oldest: int = Field(OLDEST, ge=1990)


class BackfillResult(Model):
    """The segments added, newest first, until no history was left."""

    segments: list[BuildResult]
    status: Literal["complete"]


def data_build_estimate(request: BuildEstimateRequest, context: Context = Context()) -> BuildEstimate:
    """A first build's trading days, requests and expected seconds; sends no request."""
    history = BaoStockRaw.at(context.store().home).root / "history"
    already = sum(1 for _ in history.glob("*/*.parquet")) if history.is_dir() else 0
    todo = max(STOCKS - already, 0)
    seconds = todo * SECONDS_PER_HISTORY[request.years] + STOCKS * SECONDS_PER_FACTOR + SECONDS_INDEXES
    return BuildEstimate(
        years=request.years,
        days=request.years * TRADING_DAYS,
        requests=todo + STOCKS + 2 * 12 * request.years + 10,
        seconds=round(seconds),
        already=already,
    )


def data_build(request: BuildRequest, context: Context = Context()) -> BuildResult:
    """Build the last `years` years from BaoStock into an empty data root as its first version; a stopped build
    carries on from what it fetched, and a cancel leaves no version."""
    started, store = time.monotonic(), context.store()
    if (found := store.manifest()) is not None:  # no BaoStock session for a data root that has data
        return _result(Built("exists", found["version"]), started, context)
    work = lambda source, say: build(store, source, request.years, _now(context), say, context.cancel)  # noqa: E731
    built = _session(work, context)
    if built.version is not None:
        tidy(context)
    return _result(built, started, context)


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
            return BackfillResult(segments=segments, status="complete")
        tidy(context)
        segments.append(_result(built, started, context))


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
            with _baostock() as source:
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


@contextmanager
def _baostock() -> Iterator[BaoStock]:
    """A BaoStock session, its failures as service errors: UNREACHABLE, LOCKED store_busy, INTERRUPTED."""
    try:
        with guarded(), BaoStock() as source:
            yield source
    except UpdateCancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None
    except (StoreLocked, StalePlan) as error:
        raise ServiceError(Exit.LOCKED, str(error), [ErrorDetail(code="store_busy", message=str(error))]) from None
    except (BaoStockError, ConnectionError, TimeoutError, socket.gaierror) as error:
        message = f"BaoStock cannot be reached: {error}"
        raise ServiceError(
            Exit.UNREACHABLE, message, [ErrorDetail(code="source_unreachable", message=message)]
        ) from None


def _result(built: Built, started: float, context: Context) -> BuildResult:
    name = name_of(context.store(), built.version) if built.version is not None else None
    return BuildResult(
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
