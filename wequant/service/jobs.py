"""Background jobs under wequant serve: start a long operation as a job, follow it, cancel it, take its result (S5.2).

A job's result is exactly what its operation run directly returns, byte for byte; a failed job answers with the
ErrorBody the operation would have. GET /api/jobs/{id}/events streams a job's JobInfo as it changes.
"""

from __future__ import annotations

from typing import Literal, get_type_hints

from pydantic import ConfigDict, Field, JsonValue, RootModel

from wequant.service.backtest import BacktestResult
from wequant.service.base import (
    Context,
    DataRequest,
    ErrorBody,
    ErrorDetail,
    Model,
    manifest,
    name_of,
    not_found,
    read_version,
)
from wequant.service.details import usage, validated
from wequant.service.errors import ServiceError
from wequant.service.homes import Relocated
from wequant.service.labels import EXITS, job_text
from wequant.service.outputs import Params
from wequant.service.queue import FINAL, Job, Jobs, Kind
from wequant.service.running import imported
from wequant.service.update import UpdateResult

JobKind = Literal["backtest", "data_update", "data_relocate"]
JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
KINDS = {  # a backtest in a thread of the server, with its warm caches; the update in a worker process of its own
    "backtest": Kind("wequant.service.backtest:backtest"),
    "data_update": Kind("wequant.service.update:data_update", source="baostock", process=True),
    "data_relocate": Kind("wequant.service.homes:data_relocate"),  # a thread: it switches the server's data root
}


class JobStartRequest(Model):
    """A job to start: its kind and its operation's request."""

    kind: JobKind
    request: dict[str, JsonValue] = {}
    """The request the operation takes (backtest: a BacktestRequest, data_update: {}); a null data_version is pinned to
    the version current now, so a version committed while the job waits or runs changes nothing. An update builds on
    the version current when it plans and commits nothing if the store moved on meanwhile."""


class JobRef(Model):
    """One job."""

    id: int = Field(ge=1)


class JobsListRequest(Model):
    """Every job kept."""


class JobInfo(Model):
    """Where one job is."""

    id: int
    kind: str
    """A code of labels.job_kinds."""
    state: JobState
    request: dict[str, JsonValue]
    """The request as the job runs it, data_version pinned."""
    data_version: int | None
    data_version_name: str | None
    """Null once that version is no longer kept."""
    position: int | None
    """Place in the queue, 1 = next to start; null unless queued."""
    fraction: float
    """Done, from 0 to 1."""
    stage: str
    """A code of labels.job_stages; its template filled from params is text."""
    params: Params
    text: str
    """The stage in one Chinese sentence."""
    error: ErrorBody | None
    """Why a failed or cancelled job has no result; null otherwise."""
    created_at: str
    started_at: str | None
    ended_at: str | None
    seq: int
    """Counts the job's changes."""


class JobList(Model):
    """Running and queued jobs first, in queue order, then finished ones, newest first."""

    jobs: list[JobInfo]
    limit: int
    """Jobs that run at a time; the others wait."""


class JobOutput(RootModel[BacktestResult | UpdateResult | Relocated]):
    """A succeeded job's result: what its operation run directly returns (backtest: BacktestResult, data_update:
    UpdateResult)."""

    model_config = ConfigDict(frozen=True)


def jobs_start(request: JobStartRequest, context: Context = Context()) -> JobInfo:
    """Queue a job, its request checked now and its data version pinned; it starts when a slot is free."""
    jobs = _queue(context)
    job = jobs.submit(request.kind, _pinned(request, jobs.kinds[request.kind], context))
    return job_info(job, jobs.all(), context)


def jobs_list(request: JobsListRequest, context: Context = Context()) -> JobList:
    """Every job kept: running and queued ones first, then finished ones, newest first."""
    jobs = _queue(context)
    every = jobs.all()
    order = sorted(every, key=lambda job: (job.state in FINAL, job.id if job.state not in FINAL else -job.id))
    return JobList(jobs=[job_info(job, every, context) for job in order], limit=jobs.limit)


def jobs_show(request: JobRef, context: Context = Context()) -> JobInfo:
    """Where one job is."""
    jobs = _queue(context)
    every = jobs.all()
    return job_info(_job(every, request.id), every, context)


def jobs_cancel(request: JobRef, context: Context = Context()) -> JobInfo:
    """Cancel a job: a queued one never starts, a running one stops and leaves nothing; a finished one stays as it is."""
    jobs = _queue(context)
    if jobs.cancel(request.id) is None:
        raise _unknown(request.id)
    every = jobs.all()
    return job_info(_job(every, request.id), every, context)


def jobs_result(request: JobRef, context: Context = Context()) -> JobOutput:
    """A succeeded job's result; a failed or cancelled job's ErrorBody; NOT_FOUND while it has not finished."""
    jobs = _queue(context)
    job = _job(jobs.all(), request.id)
    if job.state == "succeeded":
        return JobOutput.model_validate_json(jobs.result(job.id))
    if job.error is not None:
        body = ErrorBody.model_validate(job.error)
        raise ServiceError(body.exit, body.message, body.detail, body.complete)
    message = f"job {job.id} is {job.state}; its result comes when it has finished"
    raise not_found("job_unfinished", message, ["id"])


def job_info(job: Job, every: list[Job], context: Context) -> JobInfo:
    """The JobInfo of job, every job giving its place in the queue."""
    version = job.request.get("data_version")
    params = _params(job, every)
    return JobInfo(
        id=job.id,
        kind=job.kind,
        state=job.state,
        request=job.request,
        data_version=version,
        data_version_name=None if version is None else name_of(context.store(), version),
        position=params.get("position"),
        fraction=job.fraction,
        stage=job.stage,
        params=params,
        text=job_text(job.stage, params),
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        ended_at=job.ended_at,
        seq=job.seq,
    )


def _params(job: Job, every: list[Job]) -> dict:
    """The stage's params: the queue's and the worker's, a queued job's place, a failed job's reason in Chinese."""
    if job.stage == "queued":
        return {"position": sum(other.state == "queued" and other.id <= job.id for other in every)}
    if job.stage == "failed":
        return {"reason": EXITS[ErrorBody.model_validate(job.error).name][1]}
    return job.params


def _pinned(request: JobStartRequest, kind: Kind, context: Context) -> dict:
    """The job's request as JSON: checked against the operation's model (errors located under request), its null
    data_version set to the current version."""
    model = get_type_hints(imported(kind.operation))["request"]
    try:
        parsed = validated(model, request.request)
    except ServiceError as error:
        raise usage([_under_request(d) for d in error.detail], error.complete) from None
    pinned = parsed.model_dump(mode="json", exclude_unset=True)
    if isinstance(parsed, DataRequest) and parsed.data_version is None:
        pinned["data_version"] = manifest(context.store(), read_version(context, None))["version"]
    return pinned


def _under_request(detail: ErrorDetail) -> ErrorDetail:
    return detail.model_copy(update={"loc": ["request", *detail.loc]})


def _queue(context: Context) -> Jobs:
    if context.jobs is None:
        message = "background jobs run under wequant serve; run the operation itself instead"
        raise usage([ErrorDetail(code="no_job_queue", message=message)])
    return context.jobs


def _job(every: list[Job], id: int) -> Job:
    found = next((job for job in every if job.id == id), None)
    if found is None:
        raise _unknown(id)
    return found


def _unknown(id: int) -> ServiceError:
    return not_found("unknown_job", f"no job {id} is kept", ["id"])
