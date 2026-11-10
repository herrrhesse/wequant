"""The commands: each turns its options into a request, calls one operation and prints the result."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import App, Group, Parameter
from pydantic import BaseModel

from wequant import service
from wequant.cli.render import error_panel, render, render_schema
from wequant.service import Check, Context, Exit, ServiceError
from wequant.version import code_version

log = logging.getLogger("wequant")
FLAT = Parameter(name="*")  # a request model's fields become the command's options

app = App(
    name="wequant",
    help="WeQuant 微框: local A-share stock selection and backtesting.",
    version=code_version,
    help_format="plaintext",  # the help texts are plain text, not markdown
    default_parameter=Parameter(negative=(), consume_multiple=True),  # no --no-x or --empty-x flags; --codes A B
    error_formatter=error_panel,
)
data = app.command(App(name="data", help="The store: status, bars, data issues and the price-limit check."))
expr = app.command(App(name="expr", help="Signal expressions in canonical JSON form."))


@Parameter(name="*", group=Group("Common options", sort_key=1))
@dataclass(frozen=True, kw_only=True)
class Common:
    """Options of every command; they configure the call and are never part of a request."""

    json: bool = False
    """Print the result model as JSON."""
    verbose: Annotated[int, Parameter(alias="-v", count=True)] = 0
    """Log more to stderr: -v info, -vv debug."""
    home: Path | None = None
    """Data root (default: $WEQUANT_HOME, else ~/.wequant)."""
    cache: Path | None = None
    """Panel cache root; panels go under CACHE/panel/ (default: HOME/cache)."""


COMMON = Common()


@data.command
def status(
    *, request: Annotated[service.StatusRequest, FLAT] = service.StatusRequest(), common: Common = COMMON
) -> int:
    """Store versions, the tables of one version and whether its limit check is current."""
    return run(service.data_status, request, common)


@data.command
def bars(*, request: Annotated[service.BarsRequest, FLAT], common: Common = COMMON) -> int:
    """Panel fields of a few codes over a date range, with each day's status."""
    return run(service.data_bars, request, common)


@data.command
def issues(
    *, request: Annotated[service.IssuesRequest, FLAT] = service.IssuesRequest(), common: Common = COMMON
) -> int:
    """Rows the imports dropped, changed or flagged (data_issues)."""
    return run(service.data_issues, request, common)


@data.command
def check(*, request: Annotated[service.CheckRequest, FLAT] = service.CheckRequest(), common: Common = COMMON) -> int:
    """Price-limit check of the current version; exit 1 unless the store records it."""
    return run(service.data_check, request, common)


@expr.command(name="check")
def expr_check(*, request: Annotated[service.ExprRequest, FLAT], common: Common = COMMON) -> int:
    """Check a form without data: its canonical key and kind."""
    return run(service.expr_check, request, common)


@expr.command(name="eval")
def expr_eval(*, request: Annotated[service.EvalRequest, FLAT], common: Common = COMMON) -> int:
    """Evaluate a form for a few codes over [start, end]."""
    return run(service.expr_eval, request, common)


@app.command
def schema(
    *, request: Annotated[service.SchemaRequest, FLAT] = service.SchemaRequest(), common: Common = COMMON
) -> int:
    """JSON Schema of every request, result and failure model; the frontend's types are generated from it."""
    return run(service.schema, request, common, human=render_schema)


def run(operation: Callable, request: BaseModel, common: Common, human: Callable = render) -> int:
    """Call operation and print its result as JSON or as human(result); the exit code."""
    with _logging_to_stderr(common.verbose):
        log.debug("%s %s", operation.__name__, request.model_dump_json())
        try:
            result = operation(request, Context(common.home, common.cache))
            text = result.model_dump_json() if common.json else human(result)
        except ServiceError as error:
            log.error("%s", error)
            return error.exit
        except Exception:
            log.exception("internal error")
            return Exit.INTERNAL
    _print(text)
    return Exit.PROBLEMS if isinstance(result, Check) and not result.ok else Exit.OK


def _print(text: str) -> None:
    """text on stdout; a reader that closes the pipe early (| head) is not an error."""
    try:
        print(text, flush=True)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # keeps the interpreter's last flush quiet


@contextmanager
def _logging_to_stderr(verbose: int) -> Iterator[None]:
    """wequant's log records on this call's stderr: warnings, -v info, -vv debug; the logger is restored after."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    level = log.level
    log.addHandler(handler)
    log.setLevel(max(logging.DEBUG, logging.WARNING - 10 * verbose))
    try:
        yield
    finally:
        log.removeHandler(handler)
        log.setLevel(level)
