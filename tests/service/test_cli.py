import io
import json
from importlib.metadata import entry_points
from typing import get_type_hints

import pytest

from tests.service.conftest import MOM
from wequant import service
from wequant.cli import commands
from wequant.service import Context
from wequant.version import code_version

CASES = [
    (["data", "status"], "data_status", service.StatusRequest(), "home"),
    (
        ["data", "bars", "--codes", "300001.SZ", "600000.SH"],
        "data_bars",
        service.BarsRequest(codes=["300001.SZ", "600000.SH"]),
        "home",
    ),
    (["data", "issues"], "data_issues", service.IssuesRequest(), "home"),
    (["data", "check"], "data_check", service.CheckRequest(), "limits_home"),
    (
        ["data", "delistings", "--delisting", "hold"],
        "data_delistings",
        service.DelistingsRequest(execution={"delisting": "hold"}),
        "market_home",
    ),
    (["data", "index-check"], "data_index_check", service.IndexCheckRequest(), "market_home"),
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
    home = request.getfixturevalue(root)
    result = getattr(service, name)(request_, Context(home, cache))
    expected = 1 if isinstance(result, service.Check) and not result.ok else 0
    code, out, err = cli(*argv, "--json", "--home", home, "--cache", cache)
    assert (code, out, err) == (expected, result.model_dump_json() + "\n", "")
    assert type(result).model_validate_json(out) == result
    code, out, err = cli(*argv, "--json", "--home", home, "--cache", cache, "-vv")
    assert (code, out) == (expected, result.model_dump_json() + "\n")
    assert f"DEBUG wequant: {name} {request_.model_dump_json()}" in err


def test_verbose_logs_go_to_stderr(cli, limits_home):
    code, out, err = cli("data", "check", "--home", limits_home, "-v")
    assert code == 1 and "INFO wequant.service.check: checked store v1: 10 bars, 1 breaches" in err
    assert "INFO" not in out and "DEBUG" not in err


def test_human_output_of_bars(cli, home):
    argv = ["data", "bars", "--codes", "600000.SH", "--start", "2024-01-04", "--end", "2024-01-08"]
    code, out, _ = cli(*argv, "--fields", "close", "adj_close", "--home", home)
    assert code == 0 and out == (
        "data_version: 1\n"
        'fields: ["close", "adj_close"]\n'
        "\n"
        "date        code       status     close  adj_close\n"
        "2024-01-04  600000.SH  missing\n"
        "2024-01-05  600000.SH  suspended\n"
        "2024-01-08  600000.SH  traded     5.1    10.2\n"
    )


def test_human_output_of_expr_check(cli):
    assert cli("expr", "check", "--form", '["MA","adj_close",20]') == (
        0,
        'key: ["MA","adj_close",20]\nkind: num\n',
        "",
    )


def test_human_output_of_a_check_ends_with_its_report(cli, limits_home):
    code, out, _ = cli("data", "check", "--home", limits_home)
    assert code == 1 and out.startswith("ok: false\ndata_version: 1\ncurrent: false\n")
    assert "\n\n# Price-limit check, store v1\n" in out and "report_path" not in out


def test_help_and_version(cli):
    code, out, _ = cli("--help")
    assert code == 0 and "data" in out and "expr" in out
    code, out, _ = cli("data", "bars", "--help")
    options = ["--codes", "--start", "--end", "--fields", "--data-version", "--json", "--verbose", "--home", "--cache"]
    assert code == 0 and all(option in out for option in options)
    code, out, _ = cli("data", "check", "--help")
    assert code == 0 and "--commit" in out and "--data-version" not in out
    code, out, _ = cli("expr", "check", "--help")
    assert code == 0 and "--form" in out and '["MA","adj_close",20]' in out
    assert cli("--version") == (0, code_version() + "\n", "")


def test_cache_puts_panels_where_it_says(cli, home, tmp_path):
    cache = tmp_path / "elsewhere"
    code, _, _ = cli("data", "bars", "--codes", "600000.SH", "--home", home, "--cache", cache)
    assert code == 0 and len(list((cache / "panel").iterdir())) == 1 and not (home / "cache").exists()


def test_the_console_script():
    (script,) = entry_points(group="console_scripts", name="wequant")
    assert script.value == "wequant.cli:main"


def test_a_reader_closing_the_pipe_early_is_not_an_error(monkeypatch, tmp_path):
    def closed(*args, **kwargs):
        raise BrokenPipeError

    devnull = (tmp_path / "out").open("w")
    monkeypatch.setattr("builtins.print", closed)
    monkeypatch.setattr("sys.stdout", devnull)
    commands._print("rows")  # no traceback, no exception


@pytest.mark.parametrize("how", ["file", "stdin", "saved"])
def test_strategy_takes_a_file_stdin_or_a_saved_version(cli, library_home, monkeypatch, tmp_path, how):
    path = tmp_path / "mom.json"
    path.write_text(json.dumps(MOM))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(MOM)))
    given = {"file": path, "stdin": "-", "saved": "s@1"}[how]
    code, out, _ = cli("strategy", "validate", "--strategy", given, "--json", "--home", library_home)
    expected = service.strategy_validate(service.StrategyRequest(saved={"id": "s", "n": 1}), Context(library_home))
    assert code == 0 and service.StrategyCheck.model_validate_json(out).strategy_hash == expected.strategy_hash


def test_save_and_archive_change_the_library(cli, library_home, tmp_path):
    path = tmp_path / "plain.json"
    path.write_text("{}")
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
    argv = ["screen", "--strategy", "m", "--json", "--home", market_home]
    code, plain, _ = cli(*argv)
    code, out, err = cli(*argv, "--profile")
    report = service.ProfileReport.model_validate_json(err)
    assert code == 0 and out == plain and report.operation == "screen"
    assert {"open_panel", "evaluate:mom", "frames", "result", "render"} <= {stage.name for stage in report.stages}


RUNS = [
    (["runs", "list", "--limit", "5"], "runs_list", {"limit": 5}),
    (["runs", "show", "{id}"], "runs_show", {"id": "{id}"}),
    (["runs", "rerun", "{id}"], "runs_rerun", {"id": "{id}"}),
    (["runs", "diff", "{id}", "{id}"], "runs_diff", {"a": "{id}", "b": "{id}"}),
    (["runs", "orders", "{id}", "--side", "buy"], "runs_orders", {"id": "{id}", "side": "buy"}),
    (["runs", "day", "{id}", "2024-01-08"], "runs_day", {"id": "{id}", "date": "2024-01-08"}),
    (["runs", "anomalies", "{id}", "--channel", "fill"], "runs_anomalies", {"id": "{id}", "channel": "fill"}),
]


@pytest.mark.parametrize("argv, name, fields", RUNS, ids=[" ".join(case[0][:2]) for case in RUNS])
def test_runs_json_is_exactly_the_result_model(cli, market_home, argv, name, fields):
    context = Context(market_home)
    run_id = service.backtest(service.BacktestRequest(saved={"id": "m"}), context).run_id
    operation = getattr(service, name)
    model = get_type_hints(operation)["request"]
    fields = {key: value.format(id=run_id) if isinstance(value, str) else value for key, value in fields.items()}
    expected = operation(model.model_validate(fields), context)
    code, out, _ = cli(*[arg.format(id=run_id) for arg in argv], "--json", "--home", market_home)
    assert (code, type(expected).model_validate_json(out)) == (0, expected)
