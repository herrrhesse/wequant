import sys
from pathlib import Path

import pytest

from wequant.cli import main
from wequant.service import ErrorBody, ErrorDetail, Exit, ServiceError

README = Path(__file__).resolve().parents[2] / "README.md"
REF = '["REF","close",-1]'
A = "600000.SH"

CASES = [  # one per path to an exit code; what each operation raises is tested in the service tests
    (Exit.OK, ["data", "status", "--home", "H"]),
    (Exit.PROBLEMS, ["data", "check", "--home", "L"]),
    (Exit.USAGE, ["data", "bars", "--codes", A, "--bogus"]),  # the parser
    (Exit.USAGE, ["data", "bars", "--codes", "600000"]),  # the request's validation
    (Exit.USAGE, ["expr", "check", "--form", REF]),  # a ServiceError of the operation
    (Exit.USAGE, ["strategy", "show", "s@x", "--home", "S"]),  # the command's own ID@N parsing
    (Exit.USAGE, ["strategy", "validate", "--strategy", "missing.json", "--home", "S"]),  # the command's file reading
    (Exit.NOT_FOUND, ["data", "bars", "--codes", "600009.SH", "--home", "H"]),
]


@pytest.mark.parametrize("code, argv", CASES, ids=[" ".join(argv) for _, argv in CASES])
def test_exit_codes(cli, home, limits_home, library_home, cache, code, argv):
    """The fixed exit codes; a failure leaves stdout empty and says why on stderr."""
    roots = {"H": home, "L": limits_home, "S": library_home}
    exit, out, err = cli(*[roots.get(arg, arg) for arg in argv], "--cache", cache)
    assert exit == code
    if code >= Exit.USAGE:
        assert out == "" and err


class Unprintable:
    """An operation's result that cannot be rendered: a bug in a renderer."""

    def model_dump(self, **kwargs):
        raise ZeroDivisionError("bug")

    model_dump_json = model_dump


DETAIL = ErrorDetail(code="no_answer", message="no answer for 600000.SH", loc=["codes", 0])
BUG = ErrorBody(exit=Exit.INTERNAL, name="INTERNAL", message="ZeroDivisionError: bug")
OUTCOMES = [  # what the operation raises or returns, and the ErrorBody --json prints
    (
        ServiceError(Exit.UNREACHABLE, "source down", [DETAIL], complete=False),
        ErrorBody(exit=Exit.UNREACHABLE, name="UNREACHABLE", message="source down", detail=[DETAIL], complete=False),
    ),
    (ZeroDivisionError("bug"), BUG),
    (Unprintable(), BUG),  # b6c9a93: rendering runs inside the error handling
    (KeyboardInterrupt(), ErrorBody(exit=Exit.INTERRUPTED, name="INTERRUPTED", message="interrupted by Ctrl-C")),
]


@pytest.mark.parametrize("outcome, body", OUTCOMES, ids=["service error", "bug", "render bug", "ctrl-c"])
def test_what_an_operation_raises_decides_the_exit(cli, monkeypatch, outcome, body):
    """A ServiceError exits with its code, any other exception or a failed render 70 with the traceback on stderr,
    Ctrl-C 130; stdout stays empty, or under --json holds the failure's ErrorBody (Ctrl-C's included)."""

    def operation(request, context):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr("wequant.service.data_status", operation)
    exit, out, err = cli("data", "status")
    assert (exit, out) == (body.exit, "")
    assert body.message in err and ("Traceback" in err) == (body.exit == Exit.INTERNAL)
    exit, out, _ = cli("data", "status", "--json")
    assert exit == body.exit and ErrorBody.model_validate_json(out) == body


FAILURES = [  # (argv, the details' codes and locs, complete)
    (["data", "bars", "--codes", "600000"], [("wrong_type", ["codes", 0])], False),
    (["data", "bars"], [], True),  # --codes missing
]


@pytest.mark.parametrize("argv, details, complete", FAILURES, ids=["request", "parser"])
def test_json_prints_the_error_body_of_a_failure(cli, argv, details, complete):
    """A command line the parser or the request's validation refuses exits 2 with its ErrorBody on stdout."""
    exit, out, err = cli(*argv, "--json")
    body = ErrorBody.model_validate_json(out)
    assert exit == body.exit == Exit.USAGE and body.name == "USAGE" and err
    assert ([(d.code, d.loc) for d in body.detail], body.complete) == (details, complete)


def test_a_missing_cli_extra_exits_70(monkeypatch, capsys):
    """Without the cli extra (cyclopts, pydantic) the command exits 70 with the install hint, not Python's 1."""
    monkeypatch.setitem(sys.modules, "cyclopts", None)
    monkeypatch.delitem(sys.modules, "wequant.cli.commands", raising=False)
    with pytest.raises(SystemExit) as exited:
        main([])
    assert exited.value.code == Exit.INTERNAL
    assert "uv sync --extra cli" in capsys.readouterr().err


def test_the_readme_lists_every_exit_code():
    """The exit codes are a contract: these values, each in README's table."""
    text = README.read_text("utf-8")
    assert {member.value for member in Exit} == {0, 1, 2, 3, 4, 5, 70, 130}
    assert all(f"\n| {member.value} | `{member.name}` |" in text for member in Exit)
