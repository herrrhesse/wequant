"""The root conftest fails a session that leaves a new file or directory in the repository root."""

from pathlib import Path

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
LEAK = "import tempfile\n\ndef test_leaks():\n    tempfile.mkdtemp(dir='.')\n"


def session(pytester, test: str):
    pytester.makeconftest(CONFTEST.read_text("utf-8"))
    pytester.makepyfile(test_case=test)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider")


def test_a_leaked_temporary_directory_fails_the_session(pytester):
    result = session(pytester, LEAK)
    result.assert_outcomes(passed=1)
    assert result.ret == 1
    result.stdout.fnmatch_lines([f"*the session left tmp* in {pytester.path}*"])
