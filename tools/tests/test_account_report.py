"""The account report runs on the golden store, writes every section without a violation and writes nothing there."""

import re

from tests.account.golden import build_store
from tools import account_report


def listing(home):
    return sorted((p.relative_to(home).as_posix(), p.stat().st_size) for p in home.rglob("*"))


def test_report_writes_every_section_and_nothing_under_the_home(tmp_path):
    home, out = tmp_path / "home", tmp_path / "account.md"
    build_store(home)
    before = listing(home)
    argv = ["--home", str(home), "--cache", str(tmp_path / "cache"), "--codes", "600000.SH,000001.SZ,688001.SH"]
    assert account_report.main([*argv, "--start", "2024-03-04", "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("# Account report: store v1") and re.search(r"peak RSS: [\d,]+ MB", text)
    for heading in ("## 1. Buy-and-hold", "## 2. Random order stream", "## 3. Speed", "## 4. Data the account"):
        assert heading in text
    assert "FAIL" not in text
    invariants = [line for line in text.splitlines() if line.split(" | ")[0].lstrip("| ") in account_report.INVARIANTS]
    assert len(invariants) == len(account_report.INVARIANTS)
    assert all(line.endswith("| 0 | PASS |") for line in invariants)
    assert listing(home) == before
