"""The head of a check report: store version, code version, start time and each check's verdict and measure."""

import datetime as dt
import re

from tools.summary import Summary
from wequant.version import code_version

ROW = re.compile(r"^\| (.+?) \| (PASS|FAIL) \| (.*) \|$", re.MULTILINE)


def summary_rows(text: str) -> list[tuple[str, str, str]]:
    """(check, verdict, measured) of each row of a report's summary table."""
    return ROW.findall(text)


def test_the_head_names_the_versions_the_start_and_every_check_in_order():
    """A check report opens with the store and code versions, its start with the UTC offset, the pass count and each
    check's verdict and measure in order."""
    started = dt.datetime(2026, 11, 10, 9, 30, 5, 123, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    summary = Summary(started)
    assert summary.check("1. Speed", True, "1.89 s") == "PASS"
    assert summary.check("2. Re-run", False, "byte-identical no") == "FAIL"
    head = "\n".join(summary.head("Backtest report: store v3", 3))
    assert head.startswith("# Backtest report: store v3\n\n- **Store version:** 3\n")
    assert f"- **Code version:** {code_version()}\n- **Started:** 2026-11-10T09:30:05+01:00\n" in head
    assert "1 of 2 checks pass." in head
    assert summary_rows(head) == [("1. Speed", "PASS", "1.89 s"), ("2. Re-run", "FAIL", "byte-identical no")]
