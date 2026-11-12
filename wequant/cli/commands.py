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
from typing import Annotated, Literal

from cyclopts import App, CycloptsError, Group, Parameter
from pydantic import BaseModel

from wequant import service
from wequant.account.execution import DELISTING, PRICES
from wequant.backtest import ANOMALY_MODES, Progress
from wequant.backtest.anomalies import CHANNELS
from wequant.cli.render import error_panel, refused, render, render_schema, table
from wequant.service import Check, Context, ErrorBody, ErrorDetail, Exit, ServiceError
from wequant.service.base import error_body
from wequant.service.details import validated
from wequant.service.profile import ProfileReport, Timer
from wequant.version import code_version

log = logging.getLogger("wequant")
FLAT = Parameter(name="*")  # a request model's fields become the command's options
DEFAULT_PORT = 8731  # wequant serve's port; README states it
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SERVER_MODULES = ("fastapi", "uvicorn", "starlette", "anyio")  # what the server extra installs
INTERRUPTED = ErrorBody(exit=Exit.INTERRUPTED, name=Exit.INTERRUPTED.name, message="interrupted by Ctrl-C")

app = App(
    name="wequant",
    help="WeQuant 微框: local A-share stock selection and backtesting.",
    version=code_version,
    help_format="plaintext",  # the help texts are plain text, not markdown
    default_parameter=Parameter(negative=(), consume_multiple=True),  # no --no-x or --empty-x flags; --codes A B
    error_formatter=error_panel,
)
data = app.command(
    App(name="data", help="The store: status, bars, data issues, the price-limit check, delistings, index members.")
)
expr = app.command(App(name="expr", help="Signal expressions in canonical JSON form."))
strategies = app.command(App(name="strategy", help="Strategies and their saved versions."))
verify = data.command(
    App(name="verify", help="Verification records: what each source says about a cell, and who checked.")
)
resolve = data.command(
    App(name="resolve", help="Decisions on merge conflicts in the data root's resolutions file; never the store.")
)
runs = app.command(App(name="runs", help="The run archive: list, show, rerun, diff, orders, a day, anomalous data."))
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
    profile: bool = False
    """Time each stage; the timings go to stderr (one ProfileReport JSON line with --json)."""


COMMON = Common()


@data.command
def status(
    *, request: Annotated[service.StatusRequest, FLAT] = service.StatusRequest(), common: Common = COMMON
) -> int:
    """Store versions, the tables of one version and whether its limit check is current."""
    return run(service.data_status, request, common)


@data.command
def calendar(
    *, request: Annotated[service.CalendarRequest, FLAT] = service.CalendarRequest(), common: Common = COMMON
) -> int:
    """The trading days of a store version, with its first and last."""
    return run(service.data_calendar, request, common)


@data.command
def securities(
    *, request: Annotated[service.SecuritiesRequest, FLAT] = service.SecuritiesRequest(), common: Common = COMMON
) -> int:
    """Codes starting with --query, or whose name contains it."""
    return run(service.data_securities, request, common)


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


@data.command
def delistings(
    *,
    delisting: Literal[DELISTING] | None = None,
    failure_recovery: float | None = None,
    data_version: int | None = None,
    common: Common = COMMON,
) -> int:
    """Every delisted code: its kind and the basis for it (the rule or an override), and how a holding is treated.

    Parameters
    ----------
    delisting
        The treatment rule: auto (default), write_down, sell_at_last_close or hold.
    failure_recovery
        Share of its last close a written-down holding returns as cash (default 0).
    data_version
        Store version to read (default: the current one).
    """
    given = {"delisting": delisting, "failure_recovery": failure_recovery}
    fields = {"data_version": data_version, "execution": {k: v for k, v in given.items() if v is not None}}
    return run(service.data_delistings, lambda: validated(service.DelistingsRequest, fields), common)


@data.command
def names(*, request: Annotated[service.NamesRequest, FLAT] = service.NamesRequest(), common: Common = COMMON) -> int:
    """Stock names and official listing and delisting dates from the raw stock_basic; written only with --commit."""
    return run(service.data_names, request, common)


@data.command
def update(
    *, request: Annotated[service.UpdateRequest, FLAT] = service.UpdateRequest(), common: Common = COMMON
) -> int:
    """Fetch BaoStock's closed trading days after the store's last one and merge them as one version; exit 1 when
    conflicts wait for a decision or BaoStock's rows fail the checks, 4 when BaoStock cannot be reached."""
    return run(service.data_update, request, common)


@data.command(name="index-check")
def index_check(
    *, request: Annotated[service.IndexCheckRequest, FLAT] = service.IndexCheckRequest(), common: Common = COMMON
) -> int:
    """Each index's daily member count against its nominal size; exit 1 when any run of days is off."""
    return run(service.data_index_check, request, common)


@verify.command(name="submit")
def verify_submit(source: JsonFile, /, *, common: Common = COMMON) -> int:
    """Add verification records from a JSON list or JSON Lines; records already stored are not added again.

    Parameters
    ----------
    source
        A file, or - for stdin.
    """
    return run(
        service.data_verify_submit,
        lambda: validated(service.VerifySubmitRequest, {"records": read_records(source)}),
        common,
    )


@verify.command(name="list")
def verify_list(
    *, request: Annotated[service.VerifyListRequest, FLAT] = service.VerifyListRequest(), common: Common = COMMON
) -> int:
    """Stored verification records, filtered and paged."""
    return run(service.data_verify_list, request, common)


@resolve.command(name="list")
def resolve_list(
    *, request: Annotated[service.ResolveListRequest, FLAT] = service.ResolveListRequest(), common: Common = COMMON
) -> int:
    """Entries of the resolutions file, filtered and paged, with counts by category and status."""
    return run(service.data_resolve_list, request, common)


@resolve.command(name="approve")
def resolve_approve(*, request: Annotated[service.DecisionRequest, FLAT], common: Common = COMMON) -> int:
    """Approve the selected entries with a note; refused when one of them takes undetermined."""
    return run(service.data_resolve_approve, request, common)


@resolve.command(name="reject")
def resolve_reject(*, request: Annotated[service.DecisionRequest, FLAT], common: Common = COMMON) -> int:
    """Reject the selected entries with a note."""
    return run(service.data_resolve_reject, request, common)


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
def screen(
    *,
    strategy: StrategyArg,
    date: str | None = None,
    data_version: int | None = None,
    anomalies: Literal[ANOMALY_MODES] = "flag",
    exclude_suspicious: bool = False,
    common: Common = COMMON,
) -> int:
    """One decision day's funnel, scored stocks and rejects, without holdings: what a backtest decides that day.

    Parameters
    ----------
    date
        The decision day, YYYY-MM-DD (default: the store's last trading day).
    data_version
        Store version to read (default: the current one).
    anomalies
        flag: use anomalous data and report it; exclude_day: read error-level anomalous days as missing.
    exclude_suspicious
        Also exclude suspicious-level days (with exclude_day).
    """
    data = {"anomalies": anomalies, "exclude_suspicious": exclude_suspicious}
    fields = {"date": date, "data_version": data_version, "data": data}
    return run(
        service.screen, lambda: validated(service.ScreenRequest, {**strategy_fields(strategy), **fields}), common
    )


@app.command
def backtest(
    *,
    strategy: StrategyArg,
    start: str | None = None,
    end: str | None = None,
    cash: float = 1_000_000.0,
    benchmark: str = "000300.SH",
    no_benchmark: bool = False,
    price: Literal[PRICES] | None = None,
    commission_rate: float | None = None,
    min_commission: float | None = None,
    slippage_bps: float | None = None,
    delisting: Literal[DELISTING] | None = None,
    failure_recovery: float | None = None,
    anomalies: Literal[ANOMALY_MODES] = "flag",
    exclude_suspicious: bool = False,
    data_version: int | None = None,
    no_save: bool = False,
    report: bool = False,
    common: Common = COMMON,
) -> int:
    """Run a strategy over a date range, archive the run and print its metrics (with --report, a Markdown report).

    Parameters
    ----------
    start
        Base day, YYYY-MM-DD: the first trading day on or after it (default: the panel's first day).
    end
        Last day, YYYY-MM-DD: the last trading day on or before it (default: the store's last day).
    cash
        Yuan, a whole number of fen.
    benchmark
        An index code of index_bars.
    no_benchmark
        Run without a benchmark.
    price
        Fill at the fill day's raw open (default) or close.
    commission_rate
        Of the trade value, both sides (default 0.0003).
    min_commission
        Yuan per order (default 5).
    slippage_bps
        Per side, a cost on the trade value (default 10).
    delisting
        auto (default), write_down, sell_at_last_close or hold.
    failure_recovery
        Share of its last close a written-down holding returns as cash (default 0).
    anomalies
        flag: use anomalous data and report it; exclude_day: read error-level anomalous days as missing.
    exclude_suspicious
        Also exclude suspicious-level days (with exclude_day).
    data_version
        Store version to read (default: the current one).
    no_save
        Do not archive the run.
    report
        Attach a Markdown report.
    """
    given = {"price": price, "commission_rate": commission_rate, "min_commission": min_commission}
    given |= {"slippage_bps": slippage_bps, "delisting": delisting, "failure_recovery": failure_recovery}
    fields = {
        "start": start,
        "end": end,
        "cash": cash,
        "benchmark": None if no_benchmark else benchmark,
        "execution": {name: value for name, value in given.items() if value is not None},
        "data": {"anomalies": anomalies, "exclude_suspicious": exclude_suspicious},
        "data_version": data_version,
        "save": not no_save,
        "report": report,
    }
    request = lambda: validated(service.BacktestRequest, {**strategy_fields(strategy), **fields})  # noqa: E731
    return run(service.backtest, request, common, human=lambda result: result.report or render(result))


@app.command
def explain(
    *,
    strategy: Annotated[str | None, Parameter(name="--strategy", allow_leading_hyphen=True)] = None,
    run_id: Annotated[str | None, Parameter(name="--run")] = None,
    code: str,
    date: str,
    data_version: int | None = None,
    anomalies: Literal[ANOMALY_MODES] | None = None,
    exclude_suspicious: bool = False,
    common: Common = COMMON,
) -> int:
    """Why a stock was or was not chosen on a day; with --run also what the run decided and filled.

    Parameters
    ----------
    strategy
        A JSON file, - for JSON on stdin, or ID[@N]; or give --run.
    run_id
        A run id: its strategy, store version and data options; the date may be a decision day or its fill day.
    code
        A code such as 600000.SH.
    date
        The decision day, YYYY-MM-DD.
    data_version
        Store version to read (default: the current one; not with --run).
    anomalies
        flag (default) or exclude_day (not with --run).
    exclude_suspicious
        Also exclude suspicious-level days (with exclude_day).
    """
    data = {"anomalies": anomalies or "flag", "exclude_suspicious": exclude_suspicious}
    fields = {"run": run_id, "code": code, "date": date, "data_version": data_version}
    fields["data"] = data if anomalies or exclude_suspicious else None

    def request() -> BaseModel:
        given = strategy_fields(strategy) if strategy is not None else {}
        return validated(service.ExplainRequest, {**given, **fields})

    return run(service.explain, request, common)


@runs.command(name="list")
def runs_list(
    *, request: Annotated[service.RunsListRequest, FLAT] = service.RunsListRequest(), common: Common = COMMON
) -> int:
    """Archived runs, newest first."""
    return run(service.runs_list, request, common)


@runs.command(name="show")
def runs_show(id: str, /, *, common: Common = COMMON) -> int:
    """One run: inputs, metrics, orders, delisted holdings, anomalous data, warnings, fee rules, periods (with --json
    also every day).

    Parameters
    ----------
    id
        A run id.
    """
    return run(service.runs_show, lambda: validated(service.RunRequest, {"id": id}), common, human=_run_detail)


@runs.command(name="report")
def runs_report(id: str, /, *, common: Common = COMMON) -> int:
    """An archived run's Markdown report, as backtest --report attaches it.

    Parameters
    ----------
    id
        A run id.
    """
    return run(
        service.runs_report,
        lambda: validated(service.RunRequest, {"id": id}),
        common,
        human=lambda r: r.report.rstrip("\n"),
    )


@runs.command(name="rerun")
def runs_rerun(id: str, /, *, common: Common = COMMON) -> int:
    """Run an archived run again on its store version and compare it byte for byte.

    Parameters
    ----------
    id
        A run id.
    """
    return run(service.runs_rerun, lambda: validated(service.RunRequest, {"id": id}), common)


@runs.command(name="diff")
def runs_diff(a: str, b: str, /, *, common: Common = COMMON) -> int:
    """How two runs' inputs and metrics differ.

    Parameters
    ----------
    a
        A run id.
    b
        Another run id.
    """
    return run(service.runs_diff, lambda: validated(service.RunDiffRequest, {"a": a, "b": b}), common)


@runs.command(name="orders")
def runs_orders(
    id: str,
    /,
    *,
    code: str | None = None,
    status: Literal["filled", "partial", "rejected"] | None = None,
    side: Literal["buy", "sell"] | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
    common: Common = COMMON,
) -> int:
    """A run's orders in archive order.

    Parameters
    ----------
    id
        A run id.
    code
        Only this code.
    status
        Only this status.
    side
        Only this side.
    start
        From this fill day, YYYY-MM-DD.
    end
        To this fill day, YYYY-MM-DD.
    limit
        Rows per page, 1 to 500.
    offset
        Rows to skip.
    """
    fields = {"id": id, "code": code, "status": status, "side": side, "start": start, "end": end}
    page = {"limit": limit, "offset": offset}
    return run(service.runs_orders, lambda: validated(service.OrdersRequest, fields | page), common)


@runs.command(name="day")
def runs_day(id: str, date: str, /, *, common: Common = COMMON) -> int:
    """One day of a run: holdings at the close, the decisions taken then, the orders filled that day.

    Parameters
    ----------
    id
        A run id.
    date
        A day of the run, YYYY-MM-DD.
    """
    return run(service.runs_day, lambda: validated(service.RunDayRequest, {"id": id, "date": date}), common)


@runs.command(name="anomalies")
def runs_anomalies(
    id: str,
    /,
    *,
    channel: Literal[CHANNELS] | None = None,
    code: str | None = None,
    limit: int = 100,
    offset: int = 0,
    common: Common = COMMON,
) -> int:
    """A run's uses of anomalous data in archive order.

    Parameters
    ----------
    id
        A run id.
    channel
        Only signal, fill or valuation uses.
    code
        Only this code.
    limit
        Rows per page, 1 to 500.
    offset
        Rows to skip.
    """
    fields = {"id": id, "channel": channel, "code": code, "limit": limit, "offset": offset}
    return run(service.runs_anomalies, lambda: validated(service.AnomaliesRequest, fields), common)


@app.command
def schema(
    *, request: Annotated[service.SchemaRequest, FLAT] = service.SchemaRequest(), common: Common = COMMON
) -> int:
    """JSON Schema of every request, result and failure model; the frontend's types are generated from it."""
    return run(service.schema, request, common, human=render_schema)


@app.command
def labels(
    *, request: Annotated[service.LabelsRequest, FLAT] = service.LabelsRequest(), common: Common = COMMON
) -> int:
    """The Chinese name and sentence of every code the operations return: warnings, data issues, metrics, orders."""
    return run(service.labels, request, common)


@app.command
def serve(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    home: Path | None = None,
    cache: Path | None = None,
    cache_limit: float = 8.0,
    static: Path | None = None,
) -> int:
    """Serve every operation over local HTTP (POST /api/<operation>) and the frontend at /, from one resident process,
    until Ctrl-C.

    Parameters
    ----------
    host
        Address to bind; anything but the loopback lets other machines read your data.
    port
        Port to listen on.
    home
        Data root (default: $WEQUANT_HOME, else ~/.wequant).
    cache
        Panel cache root (default: HOME/cache).
    cache_limit
        GB of evaluated expressions kept in memory between requests.
    static
        The frontend build to serve at / (default: $WEQUANT_STATIC, else the repository's frontend/dist).
    """
    try:
        from wequant.api.server import run_server  # the server extra, imported only here
    except ModuleNotFoundError as error:
        if error.name not in SERVER_MODULES:
            raise
        print(
            f"wequant serve: {error.name} is missing; install the server extra: uv sync --extra server", file=sys.stderr
        )
        return Exit.INTERNAL
    if host not in LOOPBACK:
        print(
            f"WARNING: serving on {host}: anyone who can reach it can read your data and run commands", file=sys.stderr
        )
    run_server(Context.resident(home, cache, int(cache_limit * (1 << 30))), host, port, static)
    return Exit.OK


def run(
    operation: Callable, request: BaseModel | Callable[[], BaseModel], common: Common, human: Callable = render
) -> int:
    """Call operation on request (or on what the builder request returns) and print the result as JSON or as
    human(result); the exit code. A failure's ErrorBody goes to stdout under --json."""
    with _logging_to_stderr(common.verbose):
        try:
            request = request() if callable(request) else request
            log.debug("%s %s", operation.__name__, request.model_dump_json())
            timer = Timer(operation.__name__) if common.profile else None
            hooks = {"progress": _progress if common.verbose else None, "mark": timer and timer.mark}
            result = operation(request, Context(common.home, common.cache, **hooks))
            text = result.model_dump_json() if common.json else human(result)
            if timer is not None:
                timer.mark("render")
                _profile(timer.report(), common.json)
        except ServiceError as error:
            log.error("%s", error)
            for detail in error.detail if len(error.detail) > 1 else ():
                log.error("  %s: %s", ".".join(map(str, detail.loc)) or "-", detail.message)
            return _failed(error_body(error), common)
        except Exception as error:
            log.exception("internal error")
            message = f"{type(error).__name__}: {error}"
            return _failed(ErrorBody(exit=Exit.INTERNAL, name=Exit.INTERNAL.name, message=message), common)
        except KeyboardInterrupt:  # caught here, before cyclopts would exit 130 without a body
            log.error(INTERRUPTED.message)
            return _failed(INTERRUPTED, common)
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
    except KeyboardInterrupt:  # Ctrl-C while the command line is parsed
        if "--json" in tokens:
            _print(INTERRUPTED.model_dump_json())
        sys.exit(Exit.INTERRUPTED)


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


def read_records(source: str) -> list:
    """The records in file source (or stdin for -): one JSON list, or one JSON object per line."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    except OSError as error:
        raise ServiceError(Exit.USAGE, f"cannot read {source}: {error.strerror}") from None
    try:
        found = json.loads(text)
        return found if isinstance(found, list) else [found]
    except ValueError:
        pass
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as error:
        where = "stdin" if source == "-" else source
        detail = ErrorDetail(
            code="not_json", message=f"{where} is neither JSON nor JSON Lines: {error}", loc=["records"]
        )
        raise ServiceError(Exit.USAGE, detail.message, [detail]) from None


def saved_ref(text: str) -> dict:
    """{id, n} of ID or ID@N; bad_ref if text is neither."""
    id, at, n = text.partition("@")
    if not id or (at and not (n.isdigit() and n.isascii())):
        detail = ErrorDetail(code="bad_ref", message=f"{text!r} is not ID or ID@N", loc=["saved"])
        raise ServiceError(Exit.USAGE, detail.message, [detail])
    return {"id": id, "n": int(n) if at else None}


def _run_detail(result: service.RunDetail) -> str:
    """A run without its daily rows, then its years and months."""
    periods = result.periods.model_dump(mode="json", by_alias=True)
    tables = [table(rows) for rows in periods.values() if rows]
    return "\n\n".join([render(result.model_copy(update={"daily": None, "periods": None})), *tables])


def _profile(report: ProfileReport, as_json: bool) -> None:
    """The stage timings on stderr: one JSON line, or a table."""
    rows = [{"stage": s.name, "seconds": round(s.seconds, 4), "calls": s.calls} for s in report.stages]
    text = report.model_dump_json() if as_json else f"{report.operation}: {report.seconds:.3f} s\n{table(rows)}"
    print(text, file=sys.stderr, flush=True)


def _progress(at: Progress) -> None:
    """A long run's progress as an info line on stderr (-v)."""
    log.info("day %d of %d: %s, equity %.2f", at.done, at.total, at.day, at.equity)


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


from wequant.cli import storage as _storage  # noqa: E402, F401  its commands register themselves on app
