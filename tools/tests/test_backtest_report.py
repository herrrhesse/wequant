"""The backtest report runs on a synthetic store, passes every check, shows its orders and picks, writes nothing there."""

import re

import numpy as np

from tests.backtest.market import build_store, business_days
from tools import backtest_report
from tools.tests.test_account_report import listing
from tools.tests.test_summary import summary_rows

CODES = ("000001.SZ", "000002.SZ", "600000.SH", "600004.SH")
HEADINGS = ("## 1. No signal", "## 2. Buy-and-hold", "## 3. Speed", "## 4. Typical strategies")
HEADINGS += ("## 5. Screen equals backtest", "## 6. Progress and cancel", "## Memory")


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
    home, out = tmp_path / "home", tmp_path / "backtest.md"
    seed(home)
    before = listing(home)
    argv = ["--home", str(home), "--cache", str(tmp_path / "cache"), "--years", "1", "--codes", ",".join(CODES)]
    assert backtest_report.main([*argv, "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("# Backtest report: store v1") and all(heading in text for heading in HEADINGS)
    rows = summary_rows(text[: text.index("## Setup")])
    assert len(rows) == 1 + len(CODES) + 1 + 2 + 3 + 3 + 1 and {verdict for _, verdict, _ in rows} == {"PASS"}
    assert re.fullmatch(r"[\d.]+ s, best of 3", rows[len(CODES) + 2][2]), rows[len(CODES) + 2]
    assert "FAIL" not in text and re.search(r"Peak RSS:\*\* [\d,]+ MB: PASS", text)
    typical = text[text.index("## 4.") : text.index("## 5.")]
    orders = [int(n.replace(",", "")) for n in re.findall(r"\*\*Orders:\*\* ([\d,]+)", typical)]
    picks = [int(n.replace(",", "")) for n in re.findall(r"\*\*Picks:\*\* ([\d,]+)", typical)]
    assert len(orders) == len(picks) == 3 and min(orders) > 0 and min(picks) > 0
    screens = text[text.index("## 5.") : text.index("## 6.")]
    assert (
        screens.count("PASS") == 3 and sum(int(n) for n in re.findall(r"\| \d{4}-\d\d-\d\d \| (\d+) \|", screens)) > 0
    )
    assert text[text.index("## 2.") : text.index("## 3.")].count("PASS") == len(CODES) + 1
    assert listing(home) == before
