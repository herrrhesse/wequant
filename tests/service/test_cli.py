from importlib.metadata import entry_points

import pytest

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
