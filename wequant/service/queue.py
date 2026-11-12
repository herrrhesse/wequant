"""The job queue of wequant serve (S5.2): each job runs one operation in a worker process of its own.

At most `limit` jobs run at a time and at most one per data source (BaoStock allows one session); the others wait in
the order they came. Each job is a record at <home>/jobs/<id>.json, with its result's JSON beside it once it succeeds.
Cancel asks the worker to stop, and the operation then leaves nothing behind; a worker still running `grace` seconds
later is killed. A worker that exits without a result fails its job with the exit code. A job left unfinished when the
server stops fails then, or when the next queue opens if the server was killed. Round 3's first-run build can pause and
resume as a kind whose operation continues from what is already stored: pausing is a cancel, resuming is a new job with
the same request.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wequant.data.files import atomic_write, utc_now
from wequant.service.base import error_body
from wequant.service.errors import Exit, ServiceError

log = logging.getLogger("wequant")
STATES = ("queued", "running", "succeeded", "failed", "cancelled")
FINAL = frozenset(STATES[2:])
STAGES = ("queued", "start", "cancelling", "done", "failed", "cancelled", "crashed", "interrupted")  # the queue's own
WORKER = (sys.executable, "-m", "wequant.service.worker")


@dataclass(frozen=True)
class Kind:
    """What a job of one kind runs: an operation as module:function, and the data source it holds, if any."""

    operation: str
    source: str | None = None


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
    """The queue over one data root; kinds names what each kind of job runs."""

    def __init__(
        self,
        home: Path,
        kinds: Mapping[str, Kind],
        cache: Path | None = None,
        limit: int = 1,
        grace: float = 10.0,
        keep: int = 50,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.home, self.kinds, self.cache, self.limit, self.grace, self.keep = home, kinds, cache, limit, grace, keep
        self.root, self.clock = home / "jobs", clock or utc_now
        self._lock = threading.RLock()
        self._jobs = {job.id: job for job in self._load()}
        self._workers: dict[int, subprocess.Popen] = {}
        self._followers: dict[int, threading.Thread] = {}
        self._cancelling: set[int] = set()
        self._closing, self._changes = False, 0
        for job in self._jobs.values():
            if job.state not in FINAL:
                self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))

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
        """Stop job id: a queued one never starts; a running one is asked to stop, and killed after grace seconds."""
        with self._lock:
            job = self._jobs.get(id)
            if job is None or job.state in FINAL:
                return self.get(id)
            if job.state == "queued":
                self._end(job, "cancelled", "cancelled", error=_error(Exit.INTERRUPTED, "cancelled before it started"))
                return self.get(id)
            self._cancelling.add(id)
            self._change(job, stage="cancelling", params={})
            _tell(self._workers[id], "cancel\n")
            timer = threading.Timer(self.grace, self._kill, args=(id,))
            timer.daemon = True
            timer.start()
            return self.get(id)

    def close(self) -> None:
        """Stop for good: queued jobs fail, each worker sees its input close and cancels, and is killed after grace."""
        with self._lock:
            self._closing = True
            for job in self._waiting():
                self._end(job, "failed", "interrupted", error=_error(Exit.INTERRUPTED, "the server stopped first"))
            workers, followers = list(self._workers.values()), list(self._followers.values())
        for worker in workers:
            _close(worker)
        for worker in workers:
            try:
                worker.wait(self.grace)
            except subprocess.TimeoutExpired:
                worker.kill()
        for follower in followers:
            follower.join(self.grace)

    def _next(self) -> None:
        """Start queued jobs, oldest first, while a slot is free and the data source each needs is too."""
        for job in self._waiting():
            running = [self._jobs[id] for id in self._workers]
            if self._closing or len(running) >= self.limit:
                return
            source = self.kinds[job.kind].source
            if source is None or all(self.kinds[other.kind].source != source for other in running):
                self._launch(job)

    def _launch(self, job: Job) -> None:
        order = {"operation": self.kinds[job.kind].operation, "request": job.request, "home": str(self.home)}
        worker = subprocess.Popen(
            WORKER, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", start_new_session=True
        )  # its own session: Ctrl-C in the server's terminal reaches the server, which then stops it
        _tell(worker, json.dumps(order | {"cache": self.cache and str(self.cache)}) + "\n")
        self._workers[job.id] = worker
        self._change(job, state="running", stage="start", params={}, started_at=self.clock())
        self._save(job)
        follower = threading.Thread(target=self._follow, args=(job.id, worker), name=f"job-{job.id}", daemon=True)
        self._followers[job.id] = follower
        follower.start()

    def _follow(self, id: int, worker: subprocess.Popen) -> None:
        """Take the worker's messages until it exits, then end its job and start the next."""
        last = None
        for line in worker.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                log.warning("job %s: not a message: %r", id, line)
                continue
            if "stage" not in message:
                last = message
                continue
            with self._lock:
                if id not in self._cancelling:
                    self._change(self._jobs[id], **message)
        code = worker.wait()
        with self._lock:
            del self._workers[id], self._followers[id]
            self._finish(self._jobs[id], last or {}, code)
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
