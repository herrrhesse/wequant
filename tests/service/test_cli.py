import ast
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

import pytest

from tests.service.conftest import MOM
from wequant import service
from wequant.cli import commands
from wequant.service import Context

CASES = [
    (["data", "status"], "data_status", service.StatusRequest(), "home"),
    (
        ["data", "bars", "--codes", "300001.SZ", "600000.SH"],
        "data_bars",
        service.BarsRequest(codes=["300001.SZ", "600000.SH"]),
        "home",
    ),
    (["data", "issues"], "data_issues", service.IssuesRequest(), "home"),
    (
        ["data", "calendar", "--start", "2024-01-04"],
        "data_calendar",
        service.CalendarRequest(start="2024-01-04"),
        "home",
    ),
    (["data", "securities", "--query", "6"], "data_securities", service.SecuritiesRequest(query="6"), "home"),
    (["data", "check"], "data_check", service.CheckRequest(), "limits_home"),
    (
        ["data", "delistings", "--delisting", "hold"],
        "data_delistings",
        service.DelistingsRequest(execution={"delisting": "hold"}),
        "market_home",
    ),
    (["data", "index-check"], "data_index_check", service.IndexCheckRequest(), "market_home"),
    (
        ["data", "index-bars", "--codes", "000300.SH"],
        "data_index_bars",
        service.IndexBarsRequest(codes=["000300.SH"]),
        "market_home",
    ),
    (
        ["strategy", "schedule", "--strategy", "m"],
        "strategy_schedule",
        service.ScheduleRequest(saved={"id": "m"}),
        "market_home",
    ),  # noqa: E501
    (
        ["expr", "check", "--form", '["MA","close",2]'],
        "expr_check",
        service.ExprRequest(form='["MA","close",2]'),
        "home",
    ),
    (
        ["expr", "eval", "--form", '["MA","close",2]', "--codes", "600000.SH"],
        "expr_eval",
        service.EvalRequest(form='["MA","close",2]', codes=["600000.SH"]),
        "home",
    ),
    (["schema"], "schema", service.SchemaRequest(), "home"),
    (["labels"], "labels", service.LabelsRequest(), "home"),
    (["expr", "indicators"], "expr_indicators", service.IndicatorsRequest(), "market_home"),
    (["strategy", "templates"], "strategy_templates", service.TemplatesRequest(), "home"),
    (
        ["strategy", "validate", "--strategy", "s@1"],
        "strategy_validate",
        service.StrategyRequest(saved={"id": "s", "n": 1}),
        "library_home",
    ),
    (
        ["strategy", "summary", "--strategy", "s"],
        "strategy_summary",
        service.StrategyRequest(saved={"id": "s"}),
        "library_home",
    ),
    (["strategy", "schema"], "strategy_schema", service.StrategySchemaRequest(), "library_home"),
    (["strategy", "list"], "strategy_list", service.StrategyListRequest(), "library_home"),
    (["strategy", "versions", "s"], "strategy_versions", service.VersionsRequest(id="s"), "library_home"),
    (["strategy", "show", "s@1"], "strategy_show", service.ShowRequest(id="s", n=1), "library_home"),
    (["screen", "--strategy", "m"], "screen", service.ScreenRequest(saved={"id": "m"}), "market_home"),
    (
        ["explain", "--strategy", "m", "--code", "600004.SH", "--date", "2024-01-11"],
        "explain",
        service.ExplainRequest(saved={"id": "m"}, code="600004.SH", date="2024-01-11"),
        "market_home",
    ),
    (
        ["backtest", "--strategy", "m", "--slippage-bps", "5", "--no-benchmark", "--no-save"],
        "backtest",
        service.BacktestRequest(saved={"id": "m"}, execution={"slippage_bps": 5.0}, benchmark=None, save=False),
        "market_home",
    ),
]


@pytest.mark.parametrize("argv, name, request_, root", CASES, ids=[" ".join(case[0][:2]) for case in CASES])
def test_json_is_exactly_the_result_model(cli, cache, request, argv, name, request_, root):
    """Each command builds its request from its options and calls its one operation; --json prints the result model
    exactly and the exit code follows it; logs, -vv debug included, go to stderr only."""
    home = request.getfixturevalue(root)
    result = getattr(service, name)(request_, Context(home, cache))
    expected = 1 if isinstance(result, service.Check) and not result.ok else 0
    code, out, err = cli(*argv, "--json", "--home", home, "--cache", cache)
    assert (code, out, err) == (expected, result.model_dump_json() + "\n", "")
    assert type(result).model_validate_json(out) == result
    code, out, err = cli(*argv, "--json", "--home", home, "--cache", cache, "-vv")
    assert (code, out) == (expected, result.model_dump_json() + "\n")
    assert f"DEBUG wequant: {name} {request_.model_dump_json()}" in err


def test_human_output_smoke(cli, home, limits_home):
    """Without --json a command prints text that is not JSON, with the same exit code (the layout is no contract);
    -v adds info logs, not debug, on stderr."""
    for argv, code in (
        (["data", "bars", "--codes", "600000.SH", "--home", home], 0),  # rows as a table
        (["expr", "check", "--form", '["MA","adj_close",20]'], 0),  # name: value lines
        (["data", "check", "--home", limits_home, "-v"], 1),  # a failed check with its report
    ):
        exit, out, err = cli(*argv)
        assert exit == code and out.strip() and "INFO" not in out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
    assert "INFO wequant" in err and "DEBUG" not in err


def test_a_reader_closing_the_pipe_early_is_not_an_error():
    """b6c9a93: output into a pipe whose reader has gone (`| head`) ends the command normally: exit 0, nothing on
    stderr (no traceback, no failed flush at exit)."""
    read, write = os.pipe()
    os.close(read)  # the reader is gone before the command writes
    script = "import sys; from wequant.cli import main; main(sys.argv[1:])"
    command = [sys.executable, "-c", script, "expr", "check", "--form", '"close"', "--json"]
    done = subprocess.run(command, stdout=write, stderr=subprocess.PIPE)
    os.close(write)
    assert (done.returncode, done.stderr) == (0, b"")


@pytest.mark.parametrize("how", ["file", "stdin", "saved"])
def test_strategy_takes_a_file_stdin_or_a_saved_version(cli, library_home, monkeypatch, tmp_path, how):
    """--strategy reads a JSON file, JSON on stdin (-) or a saved version ID@N."""
    path = tmp_path / "mom.json"
    path.write_text(json.dumps(MOM), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(MOM)))
    given = {"file": path, "stdin": "-", "saved": "s@1"}[how]
    code, out, _ = cli("strategy", "validate", "--strategy", given, "--json", "--home", library_home)
    expected = service.strategy_validate(service.StrategyRequest(saved={"id": "s", "n": 1}), Context(library_home))
    assert code == 0 and service.StrategyCheck.model_validate_json(out).strategy_hash == expected.strategy_hash


def test_save_and_archive_change_the_library(cli, library_home, tmp_path):
    """Wiring of strategy save (FILE, --id, --name, --note) and strategy archive (--restore)."""
    path = tmp_path / "plain.json"
    path.write_text("{}", encoding="utf-8")
    code, out, _ = cli(
        "strategy", "save", path, "--id", "t", "--name", "T", "--note", "n", "--json", "--home", library_home
    )
    saved = service.SavedVersion.model_validate_json(out)
    assert code == 0 and (saved.id, saved.n, saved.name, saved.created) == ("t", 1, "T", True)
    code, out, _ = cli("strategy", "archive", "t", "--json", "--home", library_home)
    assert code == 0 and service.StrategyInfo.model_validate_json(out).archived
    code, out, _ = cli("strategy", "archive", "t", "--restore", "--json", "--home", library_home)
    assert code == 0 and not service.StrategyInfo.model_validate_json(out).archived


def test_profile_times_the_stages_on_stderr_and_leaves_stdout_alone(cli, market_home):
    """--profile: one ProfileReport JSON line on stderr with the operation's stages and rendering; stdout unchanged."""
    argv = ["screen", "--strategy", "m", "--json", "--home", market_home]
    code, plain, _ = cli(*argv)
    code, out, err = cli(*argv, "--profile")
    report = service.ProfileReport.model_validate_json(err)
    assert code == 0 and out == plain and report.operation == "screen"
    assert {"open_panel", "evaluate:mom", "frames", "result", "render"} <= {stage.name for stage in report.stages}


RUNS = [
    (["runs", "list", "--limit", "5"], "runs_list", {"limit": 5}),
    (["runs", "show", "{id}"], "runs_show", {"id": "{id}"}),
    (["runs", "report", "{id}"], "runs_report", {"id": "{id}"}),
    (["runs", "rerun", "{id}"], "runs_rerun", {"id": "{id}"}),
    (["runs", "diff", "{id}", "{id}"], "runs_diff", {"a": "{id}", "b": "{id}"}),
    (["runs", "orders", "{id}", "--side", "buy"], "runs_orders", {"id": "{id}", "side": "buy"}),
    (["runs", "day", "{id}", "2024-01-08"], "runs_day", {"id": "{id}", "date": "2024-01-08"}),
    (["runs", "anomalies", "{id}", "--channel", "fill"], "runs_anomalies", {"id": "{id}", "channel": "fill"}),
]


@pytest.mark.parametrize("argv, name, fields", RUNS, ids=[" ".join(case[0][:2]) for case in RUNS])
def test_runs_json_is_exactly_the_result_model(cli, market_home, argv, name, fields):
    """Each runs command maps its arguments to its request and prints the result model exactly."""
    context = Context(market_home)
    run_id = service.backtest(service.BacktestRequest(saved={"id": "m"}), context).run_id
    operation = getattr(service, name)
    model = get_type_hints(operation)["request"]
    fields = {key: value.format(id=run_id) if isinstance(value, str) else value for key, value in fields.items()}
    expected = operation(model.model_validate(fields), context)
    code, out, _ = cli(*[arg.format(id=run_id) for arg in argv], "--json", "--home", market_home)
    assert (code, type(expected).model_validate_json(out)) == (0, expected)


SERVICE_SIDE = ("wequant.service", "wequant.cli", "wequant.api", "wequant.version")


@pytest.mark.parametrize("package", ["cli", "api"])
def test_the_interfaces_call_no_core_code_but_through_the_service(package):
    """The CLI and the HTTP API only call the service layer: from the core packages they may import constants
    (upper-case names) and types named only in annotations, never something they call."""
    for path in (Path(commands.__file__).parent.parent / package).glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        annotated = {id(name) for node in ast.walk(tree) for part in _annotations(node) for name in ast.walk(part)}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in annotated}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            for module, alias in zip(modules, node.names):
                if module.startswith("wequant.") and not module.startswith(SERVICE_SIDE):
                    name = alias.asname or alias.name
                    allowed = isinstance(node, ast.ImportFrom) and (name.isupper() or name not in used)
                    assert allowed, f"{path.name} imports {module} and uses it outside annotations"


def _annotations(node: ast.AST) -> list[ast.expr]:
    """The annotations node holds directly: a function's argument and return annotations, an AnnAssign's."""
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return []
    a = node.args
    arguments = [*a.posonlyargs, *a.args, *a.kwonlyargs, *filter(None, (a.vararg, a.kwarg))]
    return [part for part in (*(arg.annotation for arg in arguments), node.returns) if part is not None]
