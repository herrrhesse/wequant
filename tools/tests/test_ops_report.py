"""The operator report runs on a tiny synthetic store, writes every section and leaves the store untouched."""

import datetime as dt

from tests.frames import table_frame
from tools import ops_report
from wequant.data.store import Store

DAYS = [day for day in (dt.date(2024, 1, 1) + dt.timedelta(i) for i in range(120)) if day.weekday() < 5][:80]
SPLIT = 78  # 600000.SH halves its price on DAYS[78]: a 10-for-10 bonus issue with factor 1 -> 2
SUSPENDED = ("000001.SZ", 76)
STOCKS = {"600000.SH": "main", "000001.SZ": "main", "300750.SZ": "chinext", "830001.BJ": "bse"}


def close(code: str, t: int) -> int:
    """Close in 厘: 600000.SH front-adjusts to 10.00 + 0.01·t yuan; the others wiggle around 20 yuan."""
    if code == "600000.SH":
        return 10_000 + 10 * t if t >= SPLIT else 20_000 + 20 * t
    return 20_000 + 10 * ((7 * t + len(code)) % 13)


def bar(code: str, t: int) -> dict:
    c = close(code, t)
    return {"code": code, "date": DAYS[t], "open": c, "high": c + 50, "low": c - 50, "close": c, "volume": 100}


def seed(home) -> None:
    """Version 1 of a store with 80 days of SH, SZ and BJ bars, a suspended day and a bonus issue."""
    bars = [bar(code, t) for code in STOCKS for t in range(len(DAYS)) if (code, t) != SUSPENDED]
    securities = [
        {"code": code, "exchange": code[-2:], "board": board, "first_date": DAYS[0], "last_date": DAYS[-1]}
        for code, board in STOCKS.items()
    ]
    factors = [
        {"code": "600000.SH", "ex_date": DAYS[0], "factor": 1.0},
        {"code": "600000.SH", "ex_date": DAYS[SPLIT], "factor": 2.0},
    ]
    Store(home).commit(
        {"source": "test"},
        replace={
            "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
            "securities": table_frame("securities", securities, source="test"),
            "bars": table_frame("bars", bars, amount=1e3, preclose_derived=True, source="test"),
            "adj_factor": table_frame("adj_factor", factors, source="test"),
        },
    )


def listing(home):
    return sorted((p.relative_to(home).as_posix(), p.stat().st_size) for p in home.rglob("*"))


def test_report_writes_every_section_and_nothing_under_the_home(tmp_path, monkeypatch):
    home, out = tmp_path / "home", tmp_path / "ops.md"
    seed(home)
    before = listing(home)
    warm = ops_report.probe
    monkeypatch.setattr(ops_report, "probe", lambda numba_cache=None: warm())  # a cold compile takes seconds
    assert ops_report.main(["--home", str(home), "--cache", str(tmp_path / "cache"), "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    for heading in ("# Operator report: store v1", "## 1. Panel", "## 2. Import", "## 3. MA20", "## 4. No look-ahead"):
        assert heading in text
    assert "## 5. TDX" in text and all(f"### {code}" in text for code in ops_report.COMPARED)
    assert "80 dates × 3 codes" in text and "(BJ codes left out: 1)" in text and "99.6% of cells hold a bar" in text
    assert "| MA5 | 10.750 | 10.760 | 10.770 |" in text
    assert "FAIL" not in text and text.count("PASS") == 2 + 2 * len(ops_report.LOOK_AHEAD)
    assert listing(home) == before
