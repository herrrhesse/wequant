"""The expression report runs on a tiny synthetic store, passes every check and leaves the store untouched."""

from tools import expr_report
from tools.tests.test_ops_report import listing, seed

HEADINGS = ("## 1. Panel", "## 2. Expressions", "## 3. Cache", "## 4. No look-ahead", "## 5. Rejected")


def test_report_passes_every_check_and_writes_nothing_under_the_home(tmp_path):
    home, out = tmp_path / "home", tmp_path / "expr.md"
    seed(home)
    before = listing(home)
    assert expr_report.main(["--home", str(home), "--cache", str(tmp_path / "cache"), "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("# Expression report: store v1") and all(heading in text for heading in HEADINGS)
    assert "80 dates × 4 codes" in text and "SH 1, SZ 2, BJ 1" in text and "peak RSS" in text
    assert "| 18 | 9 |" in text
    assert "FAIL" not in text and text.count("PASS") == 9 + 1 + 12 + len(expr_report.REJECTED)
    assert listing(home) == before
