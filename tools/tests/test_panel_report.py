import datetime as dt

import polars as pl

from tests.test_panel import BARS, DAYS, TABLE_ROWS, A, D, frame, issue, seed
from tools import panel_report
from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store


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
    assert text.index("- **Rules key:** ") < text.index("## 1. Timing")
    assert "| SUSPENDED | 0 | 2 |" in text and "| MISSING | 1 | 1 |" in text
    assert listing(home) == before


def exchange_store(home):
    """The panel test store with A on exchange precloses: d5 off after a MISSING day, d6 off and flagged, d8 off.

    D gets a 2:1 step on d4 whose preclose 5.12 is the half-up rounding of 10.23 / 2, a half-fen tie.
    """
    precloses = {(A, 5): 5050, (A, 6): 5000, (A, 8): 4900, (D, 4): 5120}
    bars = [{**b, "preclose": precloses.get((b["code"], DAYS.index(b["date"]) + 1), b["preclose"])} for b in BARS]
    adj_factor = [
        *TABLE_ROWS["adj_factor"],
        {"code": D, "ex_date": DAYS[0], "factor": 1.0},
        {"code": D, "ex_date": DAYS[3], "factor": 2.0},
    ]
    data_issues = [
        *TABLE_ROWS["data_issues"],
        issue(A, 6, "event_missing", "flagged", table="adj_factor"),
        issue(A, 7, "event_unimplied", "flagged", table="adj_factor"),  # flagged without a violation
        issue(A, 8, "event_added", "event_added", table="adj_factor"),  # not a kind of the adjustment check
        issue("600002.SH", 6, "event_missing", "flagged", table="adj_factor"),  # a code the grid does not have
    ]
    rows = {**TABLE_ROWS, "bars": bars, "adj_factor": adj_factor, "data_issues": data_issues}
    tables = {name: frame(name, table_rows) for name, table_rows in rows.items()}
    tables["bars"] = tables["bars"].with_columns(preclose_derived=pl.col("code") != A)
    store = Store(home)
    store.commit({"source": "test"}, replace=tables)
    return store


def test_continuity_splits_exchange_from_derived_precloses(tmp_path):
    store = exchange_store(tmp_path / "home")
    text = "\n".join(panel_report.continuity_section(panel_report.Run(store, tmp_path / "cache", 1, 20)))
    assert "| exchange preclose | 5 | 3 | 1 | 1 |" in text
    assert "| derived preclose | 12 | 0 | 0 | 0 |" in text  # the half-fen tie on D is not a violation
    assert "Flagged cells without a violation: 1." in text
    assert "Violations neither after a MISSING day nor flagged: 1." in text
    assert "| exchange preclose | 600000.SH | 2024-01-11 | 4.90 | 2024-01-10 | 5.00 | 1 | 20.000000 |" in text
