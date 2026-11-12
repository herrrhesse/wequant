"""A job's worker process: runs one operation for the job queue and reports on standard output.

The queue writes one JSON line, the order {operation: "module:function", request, home, cache}. A later line "cancel",
or the input closing because the server is gone, asks the operation to stop. The worker writes JSON lines: {stage,
fraction, params} as the operation goes, then {result: <the result's JSON>} or {error: <ErrorBody>}. Anything else
the process prints goes to standard error, so every line on standard output is a message.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

from wequant.backtest import Progress
from wequant.data.update import STAGES as UPDATE_STAGES
from wequant.data.update import Step
from wequant.service.base import Context, ErrorBody, error_body
from wequant.service.errors import Exit, ServiceError

log = logging.getLogger("wequant")
STAGES = ("load", "backtest", *UPDATE_STAGES)  # the stages a worker reports; the queue has its own


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the queue stops the worker, never a Ctrl-C meant for the server
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s wequant worker: %(message)s")
    say = _messages()
    order = json.loads(sys.stdin.readline())
    stop = threading.Event()
    threading.Thread(target=_listen, args=(stop,), daemon=True).start()
    say(stage="load", fraction=0.0, params={})
    progress = lambda state: say(**progress_message(state))  # noqa: E731
    home, cache = (order[key] and Path(order[key]) for key in ("home", "cache"))
    context = Context(home, cache, progress=progress, cancel=stop.is_set)
    say(**outcome(_imported(order["operation"]), order["request"], context))


def outcome(operation: Callable, request: dict, context: Context) -> dict:
    """{result: JSON} of operation on request, or {error: ErrorBody} of its failure."""
    try:
        result = operation(get_type_hints(operation)["request"].model_validate(request), context)
    except ServiceError as error:
        return {"error": error_body(error).model_dump(mode="json")}
    except Exception as error:
        log.exception("internal error in %s", operation.__name__)
        body = ErrorBody(exit=Exit.INTERNAL, name=Exit.INTERNAL.name, message=f"{type(error).__name__}: {error}")
        return {"error": body.model_dump(mode="json")}
    return {"result": result.model_dump_json()}


def progress_message(state: Progress | Step) -> dict:
    """A progress report as a message: an update's step as it is, a backtest's the day it closed, of all its run
    days, and the equity then."""
    if isinstance(state, Step):
        return state._asdict()
    params = {"day": state.day.isoformat(), "done": state.done, "total": state.total, "equity": state.equity}
    return {"stage": "backtest", "fraction": state.done / state.total if state.total else 1.0, "params": params}


def _messages() -> Callable[..., None]:
    """say(**message), writing one JSON line to standard output; file descriptor 1 then points at standard error, so a
    library's print (BaoStock prints its logins) cannot break a line."""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def say(**message) -> None:
        out.write(json.dumps(message, ensure_ascii=False) + "\n")
        out.flush()

    return say


def _listen(stop: threading.Event) -> None:
    """Set stop on "cancel" or once the input closes."""
    for line in sys.stdin:
        if line.strip() == "cancel":
            break
    stop.set()


def _imported(path: str) -> Callable:
    module, name = path.split(":")
    return getattr(importlib.import_module(module), name)


if __name__ == "__main__":
    main()
