"""The expression report runs on a tiny synthetic store, passes every check and leaves the store untouched."""

from tools import expr_report
from tools.tests.test_ops_report import listing, seed


def test_report_passes_every_check_and_writes_nothing_under_the_home(tmp_path):
    home, out = tmp_path / "home", tmp_path / "expr.md"
    seed(home)
    before = listing(home)
    assert expr_report.main(["--home", str(home), "--cache", str(tmp_path / "cache"), "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("# Expression report: store v1") and "FAIL" not in text  # expression values: the expr tests
    assert listing(home) == before
