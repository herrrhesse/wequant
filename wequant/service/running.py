"""Running one operation for a job, in a thread of the server or a worker process: its outcome and progress messages."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import get_type_hints

from wequant.backtest import Progress
from wequant.data.build import STAGES as BUILD_STAGES
from wequant.data.update import STAGES as UPDATE_STAGES
from wequant.data.update import Step
from wequant.service.base import Context, error_body, failure
from wequant.service.errors import Exit, ServiceError
from wequant.service.homes import STAGES as RELOCATE_STAGES
from wequant.service.own import STAGES as OWN_STAGES

log = logging.getLogger("wequant")
STAGES = (
    "load",
    "backtest",
    *UPDATE_STAGES,
    *RELOCATE_STAGES,
    *BUILD_STAGES,
    *OWN_STAGES,
)  # the stages an operation reports; the queue's own


def imported(path: str) -> Callable:
    """The operation at module:function."""
    module, name = path.split(":")
    return getattr(importlib.import_module(module), name)


def outcome(operation: Callable, request: dict, context: Context) -> dict:
    """{result: JSON} of operation on request, or {error: ErrorBody} of its failure."""
    try:
        result = operation(get_type_hints(operation)["request"].model_validate(request), context)
    except ServiceError as error:
        return {"error": error_body(error).model_dump(mode="json")}
    except Exception as error:
        body = failure(error)
        if body.exit == Exit.INTERNAL:
            log.exception("internal error in %s", operation.__name__)
        return {"error": body.model_dump(mode="json")}
    return {"result": result.model_dump_json()}


def progress_message(state: Progress | Step) -> dict:
    """A progress report as a message: an update's step as it is, a backtest's the day it closed, of all its run
    days, and the equity then."""
    if isinstance(state, Step):
        return state._asdict()
    params = {"day": state.day.isoformat(), "done": state.done, "total": state.total, "equity": state.equity}
    return {"stage": "backtest", "fraction": state.done / state.total if state.total else 1.0, "params": params}
