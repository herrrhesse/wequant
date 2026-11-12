"""Operations for the job queue's tests; a worker process runs them as it runs the real ones."""

import datetime as dt
import os
import time
from pathlib import Path

from wequant.backtest import Progress
from wequant.service import Context, Exit, ServiceError
from wequant.service.base import Model


class Nothing(Model):
    """No fields."""


class PidFile(Model):
    """Where the operation writes its process id."""

    path: str


class Done(Model):
    """A finished operation."""

    ok: bool = True


def wait(request: Nothing, context: Context) -> Done:
    """Report one step, then wait until cancelled, which it answers as a cancelled backtest does."""
    context.progress(Progress(1, 2, dt.date(2024, 1, 2), 1000.0))
    while not context.cancel():
        time.sleep(0.01)
    raise ServiceError(Exit.INTERRUPTED, "run cancelled")


def stubborn(request: Nothing, context: Context) -> Done:
    """Report one step, then never stop by itself."""
    context.progress(Progress(1, 2, dt.date(2024, 1, 2), 1000.0))
    while True:
        time.sleep(0.05)


def pid(request: PidFile, context: Context) -> Done:
    """Write this process's id, then wait until cancelled."""
    Path(request.path).write_text(str(os.getpid()))
    return wait(Nothing(), context)
