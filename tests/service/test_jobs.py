"""Background jobs (S5.2): the queue runs each job in a worker process, one at a time and one per data source, in the
order they came; cancel and crashes end a job with a reason; a job's result is its operation's. Synthetic homes only."""

import datetime as dt
import json
import os
import signal
import time

import pytest

from wequant import service
from wequant.backtest import Progress
from wequant.data.store import Store
from wequant.service import BacktestRequest, Context, Exit, JobRef, JobStartRequest, ServiceError
from wequant.service.base import error_body
from wequant.service.jobs import KINDS
from wequant.service.queue import Jobs, Kind
from wequant.service.worker import progress_message

KINDS_FOR_TESTS = KINDS | {
    "wait": Kind("tests.service.job_ops:wait"),
    "stubborn": Kind("tests.service.job_ops:stubborn"),
    "pid": Kind("tests.service.job_ops:pid"),
    "source": Kind("tests.service.job_ops:wait", source="baostock"),
}


@pytest.fixture
def queue(tmp_path, cache):
    """make(home, **options) -> a Jobs over home; every queue made is closed after the test."""
    made = []

    def make(home=tmp_path / "home", **options):
        made.append(Jobs(home, KINDS_FOR_TESTS, cache, **{"grace": 2.0} | options))
        return made[-1]

    yield make
    for jobs in made:
        jobs.close()


def until(jobs, id, done, timeout=30.0):
    """job id once done(job) holds; fails after timeout seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done(job := jobs.get(id)):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {id} still {jobs.get(id)}")


def finished(job):
    return job.state in ("succeeded", "failed", "cancelled")


def started(job):
    return job.stage == "backtest"  # the test operations report one step once they run


def states(jobs):
    return [job.state for job in jobs.all()]


def test_a_job_s_result_is_byte_for_byte_its_operation_run_directly(market_home, cache, queue):
    jobs = queue(market_home)
    context = Context(market_home, cache, jobs=jobs)
    info = service.jobs_start(JobStartRequest(kind="backtest", request={"saved": {"id": "m"}}), context)
    until(jobs, info.id, finished)
    direct = service.backtest(BacktestRequest(saved={"id": "m"}), Context(market_home, cache)).model_dump_json()
    assert jobs.result(info.id) == direct
    assert service.jobs_result(JobRef(id=info.id), context).model_dump_json() == direct


def test_a_failed_job_answers_with_the_error_its_operation_gives(market_home, cache, queue):
    jobs = queue(market_home)
    context = Context(market_home, cache, jobs=jobs)
    info = service.jobs_start(JobStartRequest(kind="backtest", request={"saved": {"id": "nope"}}), context)
    until(jobs, info.id, finished)
    with pytest.raises(ServiceError) as direct:
        service.backtest(BacktestRequest(saved={"id": "nope"}), Context(market_home, cache))
    with pytest.raises(ServiceError) as job:
        service.jobs_result(JobRef(id=info.id), context)
    assert error_body(job.value) == error_body(direct.value) and jobs.get(info.id).state == "failed"


def test_a_job_reads_the_data_version_current_when_it_was_started(market_home, cache, queue):
    """A version committed while the job waits changes nothing: the job runs on the version it pinned."""
    jobs = queue(market_home)
    context = Context(market_home, cache, jobs=jobs)
    blocker = jobs.submit("wait", {})
    info = service.jobs_start(JobStartRequest(kind="backtest", request={"saved": {"id": "m"}}), context)
    Store(market_home).commit({"note": "a newer version"})
    jobs.cancel(blocker.id)
    until(jobs, info.id, finished)
    result = service.jobs_result(JobRef(id=info.id), context).root
    assert (info.data_version, result.data_version, Store(market_home).versions()) == (1, 1, [1, 2])


def test_a_job_request_is_checked_when_it_is_started_and_its_errors_are_located_under_request(home, cache, queue):
    jobs = queue(home)
    with pytest.raises(ServiceError) as caught:
        service.jobs_start(JobStartRequest(kind="backtest", request={"cash": -1}), Context(home, cache, jobs=jobs))
    locs = [d.loc for d in caught.value.detail]
    assert caught.value.exit == Exit.USAGE and ["request", "cash"] in locs and jobs.all() == []


def test_without_a_queue_there_are_no_jobs(home):
    with pytest.raises(ServiceError) as caught:
        service.jobs_list(service.JobsListRequest(), Context(home))
    assert [d.code for d in caught.value.detail] == ["no_job_queue"]


def test_an_unfinished_job_has_no_result_yet(home, cache, queue):
    jobs = queue(home, limit=0)  # nothing starts
    job = jobs.submit("wait", {})
    with pytest.raises(ServiceError) as caught:
        service.jobs_result(JobRef(id=job.id), Context(home, cache, jobs=jobs))
    assert [d.code for d in caught.value.detail] == ["job_unfinished"]


def test_jobs_run_one_at_a_time_and_the_others_wait_numbered_in_the_order_they_came(home, cache, queue):
    jobs = queue(home)
    context = Context(home, cache, jobs=jobs)
    first, *_ = [jobs.submit("wait", {}) for _ in range(3)]
    listed = service.jobs_list(service.JobsListRequest(), context).jobs
    assert [(j.id, j.state, j.position) for j in listed] == [(1, "running", None), (2, "queued", 1), (3, "queued", 2)]
    jobs.cancel(first.id)
    until(jobs, 2, lambda job: job.state == "running")
    assert [service.jobs_show(JobRef(id=n), context).position for n in (2, 3)] == [None, 1]


def test_two_jobs_on_one_data_source_never_run_together(home, queue):
    jobs = queue(home, limit=2)
    ids = [jobs.submit(kind, {}).id for kind in ("source", "source", "wait")]
    until(jobs, ids[2], started)
    assert states(jobs) == ["running", "queued", "running"]


def test_a_running_job_cancelled_stops_its_operation_and_ends_cancelled(home, queue):
    jobs = queue(home)
    job = jobs.submit("wait", {})
    until(jobs, job.id, started)
    jobs.cancel(job.id)
    ended = until(jobs, job.id, finished)
    assert (ended.state, ended.stage, ended.error["exit"]) == ("cancelled", "cancelled", Exit.INTERRUPTED)


def test_a_worker_that_ignores_cancel_is_killed_after_the_grace(home, queue):
    jobs = queue(home, grace=0.3)
    job = jobs.submit("stubborn", {})
    until(jobs, job.id, started)
    jobs.cancel(job.id)
    assert until(jobs, job.id, finished, timeout=10).state == "cancelled"


def test_a_queued_job_cancelled_never_starts(home, queue):
    jobs = queue(home)
    blocker, queued = jobs.submit("wait", {}), jobs.submit("wait", {})
    assert jobs.cancel(queued.id).state == "cancelled"
    jobs.cancel(blocker.id)
    until(jobs, blocker.id, finished)
    assert (jobs.get(queued.id).state, jobs.get(queued.id).started_at) == ("cancelled", None)


def test_a_killed_worker_fails_its_job_with_its_exit_code(home, queue, tmp_path):
    jobs = queue(home)
    path = tmp_path / "pid"
    job = jobs.submit("pid", {"path": str(path)})
    until(jobs, job.id, started)
    os.kill(int(path.read_text()), signal.SIGKILL)
    ended = until(jobs, job.id, finished)
    assert (ended.state, ended.stage, ended.params, ended.error["exit"]) == (
        "failed",
        "crashed",
        {"exit_code": -signal.SIGKILL},
        Exit.INTERNAL,
    )


def test_closing_the_queue_fails_the_jobs_it_leaves_unfinished(home, queue):
    jobs = queue(home)
    running, _ = jobs.submit("wait", {}), jobs.submit("wait", {})
    until(jobs, running.id, started)
    jobs.close()
    assert [(j.state, j.stage) for j in jobs.all()] == [("failed", "interrupted")] * 2


def test_a_job_a_killed_server_left_running_fails_when_the_next_queue_opens(home, queue):
    record = {"id": 4, "kind": "wait", "request": {}, "created_at": "2024-01-02T00:00:00+00:00", "state": "running"}
    (home / "jobs").mkdir()
    (home / "jobs" / "4.json").write_text(json.dumps(record))
    job = queue(home).get(4)
    assert (job.state, job.stage, job.error["exit"]) == ("failed", "interrupted", Exit.INTERRUPTED)


def test_job_numbers_go_on_after_the_kept_jobs_and_only_the_newest_finished_are_kept(home, queue):
    jobs = queue(home, limit=0, keep=2)
    for _ in range(3):
        jobs.cancel(jobs.submit("wait", {}).id)
    reopened = queue(home, limit=0)
    assert [j.id for j in reopened.all()] == [2, 3] and reopened.submit("wait", {}).id == 4
    assert sorted(p.name for p in (home / "jobs").iterdir()) == ["2.json", "3.json", "4.json"]


def test_a_backtest_progress_report_is_the_backtest_stage_with_its_day_and_count():
    message = progress_message(Progress(5, 20, dt.date(2024, 1, 9), 1_000_500.0))
    params = {"day": "2024-01-09", "done": 5, "total": 20, "equity": 1_000_500.0}
    assert message == {"stage": "backtest", "fraction": 0.25, "params": params}
