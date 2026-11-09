"""The account report runs on the golden store, writes every section without a violation and writes nothing there."""

import math
import re

import pytest

from tests.account.fake import FakePanel
from tests.account.golden import build_store
from tools import account_report

INF, NAN = math.inf, math.nan
DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


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


@pytest.mark.parametrize(
    "open, close, down, final, unsold",
    [  # 99,900 shares bought for 999,309.69 (commission 299.70, transfer 9.99) leave 690.31 yuan
        ([10.0, 10.0, 10.5, NAN], [10.0, 10.0, 10.5, NAN], None, "1,049,640.31", "no_bar"),
        ([10.0, 10.0, 10.5, 9.45], [10.0, 10.0, 10.5, 9.5], [-INF] * 3 + [9.45], "949,740.31", "limit_down"),
        ([NAN, NAN, NAN, 10.0], [NAN, NAN, NAN, 10.2], None, "1,019,670.31", "bought on the last day"),
    ],
)
def test_a_holding_left_unsold_is_judged_at_its_last_close(open, close, down, final, unsold):
    up = None if down is None else [INF] * 4
    panel = FakePanel(DAYS, ["600000.SH"], ["main"], open, close, up=up, down=down)
    row = account_report.hold_row(panel, 0, account_report.hold(panel, 0))
    assert row[5:9] == [final, f"{final} (held: {unsold})", "0.00", "PASS"] and row[-1] == "PASS"
