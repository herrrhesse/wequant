"""The job queue of wequant serve (S5.2): each job runs one operation, in a thread of the server or in a worker process.

A job runs in a thread by default, with the server's context: it shares the warm panel and expression caches of the
plain routes, so a backtest job takes as long as the same backtest asked directly. A kind marked process runs in a
worker process of its own instead: isolated from the server, and killable when it does not stop.
At most `limit` jobs run at a time and at most one per data source (BaoStock allows one session); the others wait in
the order they came. Each job is a record at <home>/jobs/<id>.json, with its result's JSON beside it once it succeeds.
Cancel asks the operation to stop, and it then leaves nothing behind; a worker process still running `grace` seconds
later is killed. A worker that exits without a result fails its job with the exit code. A job left unfinished when the
server stops fails then, or when the next queue opens if the server was killed. Round 3's first-run build can pause and
resume as a kind whose operation continues from what is already stored: pausing is a cancel, resuming is a new job with
the same request.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wequant.data.files import atomic_write, utc_now
from wequant.service.base import Context, error_body
from wequant.service.errors import Exit, ServiceError
from wequant.service.running import imported, outcome, progress_message

log = logging.getLogger("wequant")
STATES = ("queued", "running", "succeeded", "failed", "cancelled")
FINAL = frozenset(STATES[2:])
STAGES = ("queued", "start", "cancelling", "done", "failed", "cancelled", "crashed", "interrupted")  # the queue's own
WORKER_COMMAND = "job-worker"  # the hidden wequant command a frozen build (W2) starts its workers with


@dataclass(frozen=True)
class Kind:
    """What a job of one kind runs: an operation as module:function, the data source it holds, if any, and whether it
    runs in a worker process of its own rather than a thread of the server."""

    operation: str
    source: str | None = None
    process: bool = False


@dataclass
class Job:
    """One job's record; the queue changes it under its lock, and seq counts the changes."""

    id: int
    kind: str
    request: dict
    created_at: str
    state: str = "queued"
    stage: str = "queued"
    fraction: float = 0.0
    params: dict = field(default_factory=dict)
    error: dict | None = None  # an ErrorBody's JSON once the job failed or was cancelled
    started_at: str | None = None
    ended_at: str | None = None
    seq: int = 0


class Jobs:
    """The queue over one data root; kinds names what each kind of job runs, and context is the server's, which thread
    jobs share (default: a plain context over home and cache)."""

    def __init__(
        self,
        home: Path,
        kinds: Mapping[str, Kind],
        cache: Path | None = None,
        limit: int = 1,
        grace: float = 10.0,
        keep: int = 50,
        clock: Callable[[], str] | None = None,
        context: Context | None = None,
    ) -> None:
        self.home, self.kinds, self.cache, self.limit, self.grace, self.keep = home, kinds, cache, limit, grace, keep
        self.root, self.clock = home / "jobs", clock or utc_now
        self.context = context or Context(home, cache, clock=clock)
        self._lock = threading.RLock()
        self._jobs = {job.id: job for job in self._load()}
        self._workers: dict[int, subprocess.Popen] = {}  # process jobs
        self._stops: dict[int, threading.Event] = {}  # thread jobs: set to cancel
        self._threads: dict[int, threading.Thread] = {}  # each running job's thread: its runner or its worker's reader
        self._cancelling: set[int] = set()
        self._closing, self._changes = False, 0
        for job in self._jobs.values():
            if job.state not in FINAL:
                self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))

    def rehome(self, home: Path, context: Context) -> None:
        """Keep the jobs of data root home from now on (round 3: the server switched to it): its records, with the
        running and queued jobs carried over; context is the server's new one, which later thread jobs share."""
        with self._lock:
            live = {id: job for id, job in self._jobs.items() if job.state not in FINAL}
            self.home, self.root, self.context = home, home / "jobs", context
            kept = {job.id: job for job in self._load() if job.id not in live}
            for job in kept.values():
                if job.state not in FINAL:  # its server stopped before it ended
                    self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))
            self._jobs = {**kept, **live}
            for job in live.values():
                self._save(job)
            self._changes += 1

    def submit(self, kind: str, request: dict) -> Job:
        """Queue a job of kind on request (the operation's request as JSON) and start it when a slot is free."""
        with self._lock:
            job = Job(id=max(self._jobs, default=0) + 1, kind=kind, request=request, created_at=self.clock())
            self._jobs[job.id] = job
            self._save(job)
            self._next()
            return self.get(job.id)

    def get(self, id: int) -> Job | None:
        """A copy of job id's record; None when no such job is kept."""
        with self._lock:
            job = self._jobs.get(id)
            return None if job is None else dataclasses.replace(job, params=dict(job.params))

    def all(self) -> list[Job]:
        """Copies of every job kept, by id."""
        with self._lock:
            return [self.get(id) for id in sorted(self._jobs)]

    def changes(self) -> int:
        """How often any job has changed: a follower compares it before reading a job again."""
        return self._changes

    def result(self, id: int) -> str:
        """The result's JSON of a succeeded job."""
        return self._result(id).read_text("utf-8")

    def cancel(self, id: int) -> Job | None:
        """Stop job id: a queued one never starts; a running one is asked to stop, and a worker process still running
        grace seconds later is killed."""
        with self._lock:
            job = self._jobs.get(id)
            if job is None or job.state in FINAL:
                return self.get(id)
            if job.state == "queued":
                self._end(job, "cancelled", "cancelled", error=_error(Exit.INTERRUPTED, "cancelled before it started"))
                return self.get(id)
            self._cancelling.add(id)
            self._change(job, stage="cancelling", params={})
            if id in self._stops:
                self._stops[id].set()
                return self.get(id)
            _tell(self._workers[id], "cancel\n")
            timer = threading.Timer(self.grace, self._kill, args=(id,))
            timer.daemon = True
            timer.start()
            return self.get(id)

    def close(self) -> None:
        """Stop for good: queued jobs fail, running ones are asked to stop (a worker sees its input close), and a worker
        still running after grace is killed."""
        with self._lock:
            self._closing = True
            for job in self._waiting():
                self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))
            stops, workers, threads = [*self._stops.values()], [*self._workers.values()], [*self._threads.values()]
        for stop in stops:
            stop.set()
        for worker in workers:
            _close(worker)
        for worker in workers:
            try:
                worker.wait(self.grace)
            except subprocess.TimeoutExpired:
                worker.kill()
        for thread in threads:
            thread.join(self.grace)

    def _next(self) -> None:
        """Start queued jobs, oldest first, while a slot is free and the data source each needs is too."""
        for job in self._waiting():
            running = [self._jobs[id] for id in self._threads]
            if self._closing or len(running) >= self.limit:
                return
            source = self.kinds[job.kind].source
            if source is None or all(self.kinds[other.kind].source != source for other in running):
                self._launch(job)

    def _launch(self, job: Job) -> None:
        self._change(job, state="running", stage="start", params={}, started_at=self.clock())
        self._save(job)
        kind = self.kinds[job.kind]
        runner = self._process(job, kind) if kind.process else self._thread(job, kind)
        self._threads[job.id] = threading.Thread(target=runner, name=f"job-{job.id}", daemon=True)
        self._threads[job.id].start()

    def _thread(self, job: Job, kind: Kind) -> Callable[[], None]:
        """The runner of a thread job: the operation with the server's context, its own progress and cancel."""
        stop = self._stops[job.id] = threading.Event()
        report = lambda state: self._report(job.id, progress_message(state))  # noqa: E731
        context = dataclasses.replace(self.context, progress=report, cancel=stop.is_set, jobs=None)
        request = job.request

        def run() -> None:
            message = {"error": _error(Exit.INTERNAL, "the job stopped without a result")}
            try:
                self._report(job.id, {"stage": "load", "fraction": 0.0, "params": {}})
                message = outcome(imported(kind.operation), request, context)
            finally:
                self._done(job.id, message, 0)

        return run

    def _process(self, job: Job, kind: Kind) -> Callable[[], None]:
        """The reader of a process job: it starts the worker, takes its messages until it exits, then ends the job."""
        order = {"operation": kind.operation, "request": job.request, "home": str(self.home)}
        pipes = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "text": True, "encoding": "utf-8"}
        worker = subprocess.Popen(worker_command(), **pipes, **_apart())
        _tell(worker, json.dumps(order | {"cache": self.cache and str(self.cache)}) + "\n")
        self._workers[job.id] = worker

        def follow() -> None:
            last = None
            for line in worker.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    log.warning("job %s: not a message: %r", job.id, line)
                    continue
                if "stage" in message:
                    self._report(job.id, message)
                else:
                    last = message
            self._done(job.id, last or {}, worker.wait())

        return follow

    def _report(self, id: int, message: dict) -> None:
        """A running job's progress message, unless it is being cancelled."""
        with self._lock:
            if id not in self._cancelling:
                self._change(self._jobs[id], **message)

    def _done(self, id: int, message: dict, code: int) -> None:
        """End a running job by its last message and exit code, then start the next."""
        with self._lock:
            self._workers.pop(id, None)
            self._stops.pop(id, None)
            self._threads.pop(id, None)
            self._finish(self._jobs[id], message, code)
            self._next()

    def _finish(self, job: Job, last: dict, code: int) -> None:
        cancelled = job.id in self._cancelling
        self._cancelling.discard(job.id)
        if "result" in last:
            atomic_write(self._result(job.id), last["result"].encode("utf-8"))
            self._end(job, "succeeded", "done", fraction=1.0)
        elif self._closing:
            self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))
        elif "error" in last and last["error"]["exit"] == Exit.INTERRUPTED:
            self._end(job, "cancelled", "cancelled", error=last["error"])
        elif "error" in last:
            self._end(job, "failed", "failed", error=last["error"])
        elif cancelled:
            self._end(job, "cancelled", "cancelled", error=_error(Exit.INTERRUPTED, "cancelled; the worker was killed"))
        else:
            message = f"the job's worker exited with code {code} before it finished"
            self._end(job, "failed", "crashed", params={"exit_code": code}, error=_error(Exit.INTERNAL, message))

    def _kill(self, id: int) -> None:
        with self._lock:
            worker = self._workers.get(id)
            if worker is not None and id in self._cancelling:
                worker.kill()

    def _end(self, job: Job, state: str, stage: str, **changes) -> None:
        changes = {"params": {}} | changes
        self._change(job, state=state, stage=stage, ended_at=self.clock(), **changes)
        self._save(job)
        self._prune()

    def _change(self, job: Job, **changes) -> None:
        for name, value in changes.items():
            setattr(job, name, value)
        job.seq += 1
        self._changes += 1

    def _waiting(self) -> list[Job]:
        return [job for _, job in sorted(self._jobs.items()) if job.state == "queued"]

    def _prune(self) -> None:
        """Keep the newest keep finished jobs."""
        finished = sorted((id for id, job in self._jobs.items() if job.state in FINAL), reverse=True)
        for id in finished[self.keep :]:
            del self._jobs[id]
            self._record(id).unlink(missing_ok=True)
            self._result(id).unlink(missing_ok=True)

    def _load(self) -> list[Job]:
        paths = self.root.glob("*.json") if self.root.is_dir() else []
        return [Job(**json.loads(p.read_text("utf-8"))) for p in paths if p.stem.isdigit()]

    def _save(self, job: Job) -> None:
        atomic_write(self._record(job.id), json.dumps(dataclasses.asdict(job), ensure_ascii=False).encode("utf-8"))

    def _record(self, id: int) -> Path:
        return self.root / f"{id}.json"

    def _result(self, id: int) -> Path:
        return self.root / f"{id}.result.json"


def _error(exit: Exit, message: str) -> dict:
    return error_body(ServiceError(exit, message)).model_dump(mode="json")


def worker_command() -> list[str]:
    """How to start a job's worker: this interpreter on the worker module, or a frozen program's hidden command."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_COMMAND]
    return [sys.executable, "-m", "wequant.service.worker"]


def _apart() -> dict:
    """Popen options that keep Ctrl-C in the server's terminal from reaching a worker: the server then stops it."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _tell(worker: subprocess.Popen, text: str) -> None:
    """Write text to the worker; one that already exited is followed to its end anyway."""
    try:
        worker.stdin.write(text)
        worker.stdin.flush()
    except OSError:
        pass


def _close(worker: subprocess.Popen) -> None:
    try:
        worker.stdin.close()
    except OSError:
        pass
