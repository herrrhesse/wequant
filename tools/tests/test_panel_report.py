import datetime as dt

import polars as pl

from tests.test_panel import seed
from tools import panel_report
from wequant.data.raw import BaoStockRaw


def write_snapshots(home):
    """Raw daily snapshots: two calendar days, one with a code the store lacks, and one day off the calendar."""
    raw = BaoStockRaw.at(home)
    snapshots = {
        "2024-01-04": [("sh.600000", "1"), ("sz.000001", "0"), ("sz.300001", "1"), ("sh.600009", "1")],
        "2024-01-05": [("sh.600000", "0"), ("sz.000001", "1")],
        "2024-01-12": [("sh.600000", "1")],
    }
    for day, rows in snapshots.items():
        frame = pl.DataFrame(
            {"date": [day] * len(rows), "code": [c for c, _ in rows], "tradestatus": [t for _, t in rows]}
        )
        raw.write(raw.daily(dt.date.fromisoformat(day)), frame)


def listing(home):
    return sorted((p.relative_to(home).as_posix(), p.stat().st_size) for p in home.rglob("*"))


def test_report_writes_every_section_and_nothing_under_the_home(tmp_path):
    home = tmp_path / "home"
    seed(home)
    write_snapshots(home)
    before = listing(home)
    out = tmp_path / "panel.md"
    assert panel_report.main(["--home", str(home), "--cache", str(tmp_path / "cache"), "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    for heading in ("# Panel report: store v1", "## 1. Timing", "## 2. Continuity", "## 3. Day status", "## 4. Limits"):
        assert heading in text
    assert "## 5. Invariants" in text and "FAIL" not in text
    assert "| SUSPENDED | 0 | 2 |" in text and "| MISSING | 1 | 1 |" in text
    assert listing(home) == before
