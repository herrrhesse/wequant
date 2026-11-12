"""data update: the daily update from BaoStock (S2.5), as a command, a route and a background job.

Failures leave the store as it was and answer with an exit code: UNREACHABLE when BaoStock cannot be reached or logged
in to, LOCKED while another writer or BaoStock session runs, INTERRUPTED when cancelled.
"""

from __future__ import annotations

import datetime as dt
import logging
import socket
from typing import Literal

from wequant.data.merge import RawDataError, StalePlan
from wequant.data.sources.baostock import BaoStock, BaoStockError
from wequant.data.store import StoreLocked
from wequant.data.update import Outcome, RawRejected, UpdateCancelled, update
from wequant.service.base import Check, Context, ErrorDetail, Model, manifest, named
from wequant.service.errors import Exit, ServiceError
from wequant.service.labels import update_text

log = logging.getLogger(__name__)
Status = Literal["updated", "up_to_date", "not_published", "conflicts", "rejected"]


class UpdateRequest(Model):
    """Fetch BaoStock's trading days after the store's last one and merge them as one version."""


class UpdateResult(Check):
    """What the update did; ok unless conflicts or rejected rows stopped it (the store is then unchanged)."""

    status: Status
    text: str
    """The outcome in one Chinese sentence (labels.update_statuses filled from this result)."""
    data_version: int
    data_version_name: str
    """The current version afterwards: the new one when updated."""
    previous_version: int
    days: list[dt.date]
    """Days merged; with conflicts, the days held back."""
    waiting: dt.date | None
    """The first day BaoStock has not published yet; null when every closed day is in."""
    proposed: int
    """Resolutions proposed for conflicts, waiting for approval on the data page."""
    unresolved: int
    issues: dict[str, int]
    """This update's data_issues rows by kind."""
    indexes: dict[str, int]
    """Index rows added, by code."""
    problems: list[str]
    """Why BaoStock's rows were rejected; empty otherwise."""
    requests: int
    """Requests to BaoStock, the login included."""


def data_update(request: UpdateRequest, context: Context = Context()) -> UpdateResult:
    """Fetch and merge the closed trading days after the store's last one; a second run the same day changes nothing."""
    store = context.store()
    base = manifest(store)
    now = dt.datetime.fromisoformat(context.clock()) if context.clock else dt.datetime.now(dt.UTC)
    try:
        with BaoStock() as source:
            outcome = update(store, source, now, context.progress, context.cancel)
    except (RawDataError, RawRejected) as error:
        problems = getattr(error, "problems", [str(error)])
        outcome = Outcome("rejected", base["version"])
    except UpdateCancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None
    except (StoreLocked, StalePlan) as error:
        raise ServiceError(Exit.LOCKED, str(error), [ErrorDetail(code="store_busy", message=str(error))]) from None
    except (BaoStockError, ConnectionError, TimeoutError, socket.gaierror) as error:
        message = f"BaoStock cannot be reached: {error}"
        raise ServiceError(
            Exit.UNREACHABLE, message, [ErrorDetail(code="source_unreachable", message=message)]
        ) from None
    else:
        problems = []
    log.info("update: %s, %d days, %d requests", outcome.status, len(outcome.days), outcome.requests)
    return result(outcome, problems, context)


def result(outcome: Outcome, problems: list[str], context: Context) -> UpdateResult:
    """The UpdateResult of an outcome."""
    current = manifest(context.store())
    fields = {
        "status": outcome.status,
        "previous_version": outcome.base_version,
        "days": outcome.days,
        "waiting": outcome.waiting,
        "proposed": outcome.proposed,
        "unresolved": outcome.unresolved,
        "issues": outcome.issues,
        "indexes": outcome.indexes,
        "problems": problems,
        "requests": outcome.requests,
    }
    ok = outcome.status not in ("conflicts", "rejected")
    return UpdateResult(ok=ok, text=update_text(fields), **named(current), **fields)
