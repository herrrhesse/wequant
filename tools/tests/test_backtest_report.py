"""The backtest report runs on a synthetic store, passes every check and writes nothing there."""

import re

import numpy as np

from tests.backtest.market import build_store, business_days
from tools import backtest_report
from tools.tests.test_account_report import listing
from tools.tests.test_summary import summary_rows

CODES = ("000001.SZ", "000002.SZ", "600000.SH", "600004.SH")


def seed(home) -> None:
    """100 trading days of four liquid codes on a seeded walk inside the limits, and the CSI 300."""
    rng = np.random.default_rng(11)
    days, bars = business_days(100), {}
    for code in CODES:
        closes = np.round(10 * np.cumprod(rng.uniform(0.96, 1.04, len(days))), 2)
        bars[code] = [(round(c * rng.uniform(0.99, 1.01), 2), c) for c in closes]
    index = ("000300.SH", np.round(3000 * np.cumprod(rng.uniform(0.98, 1.02, len(days))), 2).tolist())
    build_store(home, days, bars, index=index, amounts=dict.fromkeys(CODES, 3e8))


def test_report_passes_every_check_and_writes_nothing_under_the_home(tmp_path):
    """Every check passes, the speed rows give the best of 3 runs in seconds, and nothing is written under the home.

    A speed row ends "; busy" when the machine was loaded while it was timed (the report's own note); the test takes
    either form, so it does not depend on the load."""
    home, out = tmp_path / "home", tmp_path / "backtest.md"
    seed(home)
    before = listing(home)
    argv = ["--home", str(home), "--cache", str(tmp_path / "cache"), "--years", "1", "--codes", ",".join(CODES)]
    assert backtest_report.main([*argv, "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    rows = summary_rows(text[: text.index("## Setup")])
    assert rows and {verdict for _, verdict, _ in rows} == {"PASS"} and "FAIL" not in text
    speeds = [measured for check, _, measured in rows if check.startswith("3. Speed")]
    assert len(speeds) == 2 and all(re.fullmatch(r"[\d.]+ s, best of 3(; busy)?", s) for s in speeds), speeds
    assert listing(home) == before
