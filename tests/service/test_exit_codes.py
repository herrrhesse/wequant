import sys
from pathlib import Path

import pytest

from wequant.cli import main
from wequant.data.store import Store
from wequant.service import ErrorBody, Exit, ServiceError

README = Path(__file__).resolve().parents[2] / "README.md"
REF = '["REF","close",-1]'
A = "600000.SH"

CASES = [
    (Exit.OK, ["data", "status", "--home", "H"]),
    (Exit.OK, ["expr", "check", "--form", '"close"']),
    (Exit.PROBLEMS, ["data", "check", "--home", "L"]),
    (Exit.USAGE, ["data", "bars", "--home", "H"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--bogus"]),
    (Exit.USAGE, ["data", "nope"]),
    (Exit.USAGE, ["data", "bars", "--codes", "600000"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, A]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--start", "2024-01-05", "--end", "2024-01-02"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--data-version", "0"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--start", "2024-13-01"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--fields", "bogus"]),
    (Exit.USAGE, ["data", "issues", "--limit", "-1"]),
    (Exit.USAGE, ["data", "check", "--data-version", "1"]),
    (Exit.USAGE, ["data", "check", "--no-commit"]),
    (Exit.USAGE, ["expr", "check", "--form", REF]),
    (Exit.USAGE, ["expr", "check", "--form", "close"]),
    (Exit.USAGE, ["expr", "eval", "--form", REF, "--codes", A, "--home", "H"]),
    (Exit.USAGE, ["expr", "eval", "--form", "close", "--codes", A, "--home", "H"]),
    (Exit.USAGE, ["expr", "eval", "--form", '["MA","close",0]', "--codes", A, "--home", "H"]),
    (Exit.NOT_FOUND, ["data", "status", "--home", "E"]),
    (Exit.NOT_FOUND, ["data", "status", "--data-version", "9", "--home", "H"]),
    (Exit.NOT_FOUND, ["data", "issues", "--data-version", "9", "--home", "H"]),
    (Exit.NOT_FOUND, ["data", "bars", "--codes", "600009.SH", "--home", "H"]),
    (Exit.NOT_FOUND, ["data", "bars", "--codes", A, "--start", "2024-01-06", "--end", "2024-01-07", "--home", "H"]),
    (Exit.NOT_FOUND, ["expr", "eval", "--form", '"close"', "--codes", A, "--start", "2024-01-12", "--home", "H"]),
    (Exit.NOT_FOUND, ["data", "check", "--home", "E"]),
    (Exit.USAGE, ["strategy", "show", "s@x", "--home", "S"]),
    (Exit.USAGE, ["strategy", "validate", "--strategy", "missing.json", "--home", "S"]),
    (Exit.NOT_FOUND, ["strategy", "show", "s@9", "--home", "S"]),
]


@pytest.mark.parametrize("code, argv", CASES, ids=[" ".join(argv) for _, argv in CASES])
def test_exit_codes(cli, home, limits_home, library_home, cache, tmp_path, code, argv):
    roots = {"H": home, "L": limits_home, "E": tmp_path / "empty", "S": library_home}
    exit, out, err = cli(*[roots.get(arg, arg) for arg in argv], "--cache", cache)
    assert exit == code
    if code >= Exit.USAGE:
        assert out == "" and err


@pytest.mark.parametrize(
    "error, code, message",
    [
        (ServiceError(Exit.UNREACHABLE, "source down"), Exit.UNREACHABLE, "ERROR wequant: source down"),
        (ZeroDivisionError("bug"), Exit.INTERNAL, "ZeroDivisionError: bug"),
        (KeyboardInterrupt(), Exit.INTERRUPTED, ""),
    ],
)
def test_what_an_operation_raises_decides_the_exit(cli, monkeypatch, error, code, message):
    def fail(request, context):
        raise error

    monkeypatch.setattr("wequant.service.data_status", fail)
    exit, out, err = cli("data", "status")
    assert (exit, out) == (code, "") and message in err
    if code == Exit.INTERNAL:
        assert "Traceback" in err and "ERROR wequant: internal error" in err


FAILURES = [
    (
        ["data", "status", "--data-version", "9", "--home", "H"],
        Exit.NOT_FOUND,
        [("version_not_kept", ["data_version"])],
    ),
    (["data", "bars", "--codes", "600000", "--home", "H"], Exit.USAGE, [("wrong_type", ["codes", 0])]),
    (["data", "bars", "--home", "H"], Exit.USAGE, []),
]


@pytest.mark.parametrize("argv, code, details", FAILURES, ids=["service", "request", "parser"])
def test_json_prints_the_error_body_of_a_failure(cli, home, argv, code, details):
    exit, out, err = cli(*[home if arg == "H" else arg for arg in argv], "--json")
    body = ErrorBody.model_validate_json(out)
    assert exit == body.exit == code and body.name == code.name and err
    assert [(d.code, d.loc) for d in body.detail] == details


def test_json_prints_the_error_body_of_an_internal_error(cli, monkeypatch):
    def fail(request, context):
        raise ZeroDivisionError("bug")

    monkeypatch.setattr("wequant.service.data_status", fail)
    exit, out, err = cli("data", "status", "--json")
    assert exit == Exit.INTERNAL and "Traceback" in err
    assert ErrorBody.model_validate_json(out) == ErrorBody(
        exit=Exit.INTERNAL, name="INTERNAL", message="ZeroDivisionError: bug"
    )


def test_a_locked_store(cli, limits_home):
    with Store(limits_home).lock():
        exit, out, err = cli("data", "check", "--commit", "--home", limits_home)
    assert (exit, out) == (Exit.LOCKED, "") and "is held by" in err
    assert Store(limits_home).versions() == [1]


def test_a_missing_cli_extra_exits_70(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cyclopts", None)
    monkeypatch.delitem(sys.modules, "wequant.cli.commands", raising=False)
    with pytest.raises(SystemExit) as exited:
        main([])
    assert exited.value.code == Exit.INTERNAL
    assert "uv sync --extra cli" in capsys.readouterr().err


def test_the_readme_lists_every_exit_code():
    text = README.read_text("utf-8")
    assert {member.value for member in Exit} == {0, 1, 2, 3, 4, 5, 70, 130}
    assert all(f"\n| {member.value} | `{member.name}` |" in text for member in Exit)
