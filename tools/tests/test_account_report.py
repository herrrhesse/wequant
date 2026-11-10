"""The account report runs on the golden store, writes every section without a violation and writes nothing there."""

import datetime as dt
import math
import re

import polars as pl
import pytest

from tests.account.fake import FakePanel
from tests.account.golden import build_store
from tools import account_report
from wequant.account import Execution

INF, NAN = math.inf, math.nan
DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
NO_SLIPPAGE = Execution(slippage_bps=0)
NO_BAR = ([10.0, 10.0, 10.5, NAN], [10.0, 10.0, 10.5, NAN], None)  # open, close, down
LIMIT_DOWN = ([10.0, 10.0, 10.5, 9.45], [10.0, 10.0, 10.5, 9.5], [-INF] * 3 + [9.45])
LAST_DAY = ([NAN, NAN, NAN, 10.0], [NAN, NAN, NAN, 10.2], None)


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
    assert "slippage_bps=10.0" in text and "dividend tax is ignored" in text
    for heading in ("## 1. Buy-and-hold", "## 2. Random order stream", "## 3. Speed", "## 4. Data the account"):
        assert heading in text
    assert "FAIL" not in text
    invariants = [line for line in text.splitlines() if line.split(" | ")[0].lstrip("| ") in account_report.INVARIANTS]
    assert len(invariants) == len(account_report.INVARIANTS)
    assert all(line.endswith("| 0 | PASS |") for line in invariants)
    capacities = re.findall(
        r"\*\*Capacity.*max [\d.]+% \(.*\), 95th percentile [\d.]+%; fills with an unknown day amount: 0", text
    )
    assert len(capacities) == 1 + len(account_report.TOPS)
    assert listing(home) == before


@pytest.mark.parametrize(
    "execution, prices, final, unsold",
    [  # 99,900 shares bought for 999,309.69 (commission 299.70, transfer 9.99) leave 690.31 yuan
        (NO_SLIPPAGE, NO_BAR, "1,049,640.31", "no_bar"),
        (NO_SLIPPAGE, LIMIT_DOWN, "949,740.31", "limit_down"),
        (NO_SLIPPAGE, LAST_DAY, "1,019,670.31", "bought on the last day"),
        # the holding's own execution, the default 10 bp: 99,800 shares for 999,307.38 (commission 299.40, transfer
        # 9.98, slippage 998.00) leave 692.62
        (Execution(), LAST_DAY, "1,018,652.62", "bought on the last day"),
    ],
)
def test_a_holding_left_unsold_is_judged_at_its_last_close(execution, prices, final, unsold):
    open, close, down = prices
    up = None if down is None else [INF] * 4
    panel = FakePanel(DAYS, ["600000.SH"], ["main"], open, close, up=up, down=down)
    row = account_report.hold_row(panel, 0, account_report.hold(panel, 0, execution))
    assert row[5:9] == [final, f"{final} (held: {unsold})", "0.00", "PASS"] and row[-1] == "PASS"


def test_capacity_names_the_largest_known_fill():
    rows = [("filled", 100.0, NAN), ("filled", 50.0, 1000.0), ("partial", 10.0, 1000.0), ("rejected", 0.0, 1.0)]
    orders = pl.DataFrame(
        [(dt.date(2024, 1, 3), f"60000{k}.SH", *row) for k, row in enumerate(rows)],
        schema={"date": pl.Date, "code": pl.String, "status": pl.String, "value": pl.Float64, "day_amount": pl.Float64},
        orient="row",
    )
    assert account_report.capacity_text(orders) == (
        "max 5.000% (600001.SH on 2024-01-03: 50.00 of 1,000.00 yuan), 95th percentile 4.800%; "
        "fills with an unknown day amount: 1"
    )
