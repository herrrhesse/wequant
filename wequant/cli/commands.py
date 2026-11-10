"""The commands: each turns its options into a request, calls one operation and prints the result."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import App, CycloptsError, Group, Parameter
from pydantic import BaseModel

from wequant import service
from wequant.cli.render import error_panel, refused, render, render_schema
from wequant.service import Check, Context, ErrorBody, ErrorDetail, Exit, ServiceError
from wequant.service.base import error_body
from wequant.service.details import validated
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
strategies = app.command(App(name="strategy", help="Strategies and their saved versions."))
StrategyArg = Annotated[
    str, Parameter(name="--strategy", allow_leading_hyphen=True, help="A JSON file, - for JSON on stdin, or ID[@N].")
]
JsonFile = Annotated[str, Parameter(allow_leading_hyphen=True)]  # "-" is stdin, not an option


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


@strategies.command(name="validate")
def strategy_validate(*, strategy: StrategyArg, common: Common = COMMON) -> int:
    """Check a strategy: its hash, canonical form, weight shares and the saved versions with the same content."""
    return run(service.strategy_validate, lambda: validated(service.StrategyRequest, strategy_fields(strategy)), common)


@strategies.command(name="summary")
def strategy_summary(*, strategy: StrategyArg, common: Common = COMMON) -> int:
    """The strategy in one Chinese sentence."""
    request = lambda: validated(service.StrategyRequest, strategy_fields(strategy))  # noqa: E731 - built inside run
    return run(service.strategy_summary, request, common, human=lambda result: result.text)


@strategies.command(name="schema")
def strategy_schema(*, common: Common = COMMON) -> int:
    """JSON Schemas of the strategy and of the execution block."""
    return run(
        service.strategy_schema, service.StrategySchemaRequest(), common, human=lambda r: r.model_dump_json(indent=1)
    )


@strategies.command(name="save")
def strategy_save(
    file: JsonFile,
    /,
    *,
    id: str | None = None,
    name: str | None = None,
    note: str | None = None,
    common: Common = COMMON,
) -> int:
    """Save the strategy in FILE (- for stdin) as the next version of --id, or as a new strategy without --id.

    Parameters
    ----------
    file
        A strategy JSON file, or - for JSON on stdin.
    id
        The strategy to add a version to (a-z, 0-9 and -); a new one when not saved yet.
    name
        Display name; kept when not given.
    note
        A note on this version.
    """
    fields = {"id": id, "name": name, "note": note}
    return run(
        service.strategy_save, lambda: validated(service.SaveRequest, {"strategy": read_json(file), **fields}), common
    )


@strategies.command(name="list")
def strategy_list(
    *, request: Annotated[service.StrategyListRequest, FLAT] = service.StrategyListRequest(), common: Common = COMMON
) -> int:
    """The saved strategies by id."""
    return run(service.strategy_list, request, common)


@strategies.command(name="versions")
def strategy_versions(id: str, /, *, common: Common = COMMON) -> int:
    """One strategy's versions, oldest first.

    Parameters
    ----------
    id
        A saved strategy id.
    """
    return run(service.strategy_versions, lambda: validated(service.VersionsRequest, {"id": id}), common)


@strategies.command(name="show")
def strategy_show(ref: str, /, *, common: Common = COMMON) -> int:
    """One saved version with its content and summary.

    Parameters
    ----------
    ref
        ID for the current version, ID@N for version N.
    """
    return run(service.strategy_show, lambda: validated(service.ShowRequest, saved_ref(ref)), common)


@strategies.command(name="archive")
def strategy_archive(id: str, /, *, restore: bool = False, common: Common = COMMON) -> int:
    """Archive a strategy (hidden from the list, its files kept), or restore it.

    Parameters
    ----------
    id
        A saved strategy id.
    restore
        Clear the archived flag instead.
    """
    return run(
        service.strategy_archive, lambda: validated(service.ArchiveRequest, {"id": id, "restore": restore}), common
    )


@app.command
def schema(
    *, request: Annotated[service.SchemaRequest, FLAT] = service.SchemaRequest(), common: Common = COMMON
) -> int:
    """JSON Schema of every request, result and failure model; the frontend's types are generated from it."""
    return run(service.schema, request, common, human=render_schema)


def run(
    operation: Callable, request: BaseModel | Callable[[], BaseModel], common: Common, human: Callable = render
) -> int:
    """Call operation on request (or on what the builder request returns) and print the result as JSON or as
    human(result); the exit code. A failure's ErrorBody goes to stdout under --json."""
    with _logging_to_stderr(common.verbose):
        try:
            request = request() if callable(request) else request
            log.debug("%s %s", operation.__name__, request.model_dump_json())
            result = operation(request, Context(common.home, common.cache))
            text = result.model_dump_json() if common.json else human(result)
        except ServiceError as error:
            log.error("%s", error)
            for detail in error.detail if len(error.detail) > 1 else ():
                log.error("  %s: %s", ".".join(map(str, detail.loc)) or "-", detail.message)
            return _failed(error_body(error), common)
        except Exception as error:
            log.exception("internal error")
            message = f"{type(error).__name__}: {error}"
            return _failed(ErrorBody(exit=Exit.INTERNAL, name=Exit.INTERNAL.name, message=message), common)
    _print(text)
    return Exit.PROBLEMS if isinstance(result, Check) and not result.ok else Exit.OK


def launch(argv: list[str] | None = None) -> None:
    """Run the command line; one the parser refuses exits 2, with its ErrorBody on stdout under --json."""
    tokens = sys.argv[1:] if argv is None else argv
    try:
        app(tokens, exit_on_error=False)
    except CycloptsError as error:
        if "--json" in tokens:
            _print(refused(error).model_dump_json())
        sys.exit(Exit.USAGE)


def strategy_fields(text: str) -> dict:
    """The request fields of --strategy: - reads JSON on stdin, a path (with / or \\, or ending .json) its file, else
    ID[@N] is a saved version."""
    if text == "-" or "/" in text or "\\" in text or text.endswith(".json"):
        return {"strategy": read_json(text)}
    return {"saved": saved_ref(text)}


def read_json(source: str) -> object:
    """The JSON in file source, or on stdin for -; USAGE if it cannot be read or is not JSON."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
        return json.loads(text)
    except OSError as error:
        raise ServiceError(Exit.USAGE, f"cannot read {source}: {error.strerror}") from None
    except ValueError as error:
        where = "stdin" if source == "-" else source
        detail = ErrorDetail(code="not_json", message=f"{where} is not JSON: {error}", loc=["strategy"])
        raise ServiceError(Exit.USAGE, detail.message, [detail]) from None


def saved_ref(text: str) -> dict:
    """{id, n} of ID or ID@N; bad_ref if text is neither."""
    id, at, n = text.partition("@")
    if not id or (at and not (n.isdigit() and n.isascii())):
        detail = ErrorDetail(code="bad_ref", message=f"{text!r} is not ID or ID@N", loc=["saved"])
        raise ServiceError(Exit.USAGE, detail.message, [detail])
    return {"id": id, "n": int(n) if at else None}


def _failed(body: ErrorBody, common: Common) -> int:
    """body's exit code, after printing body under --json."""
    if common.json:
        _print(body.model_dump_json())
    return body.exit


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
