import polars as pl
import pytest

from tests.baostock_raw_fake import C2, D1, D2, D3, D4, day_row, standard_world, write_day
from tools import baostock_merge as tool
from wequant.data.merge import apply_merge, plan_merge
from wequant.data.raw import BaoStockRaw
from wequant.data.schema import TABLES
from wequant.data.store import CommitResult, Store

CRITERIA = (
    "prices_equal",
    "status_agrees",
    "adjustment_checked",
    "factor_files_consistent",
    "touched_only",
    "registered",
    "bj_frozen",
)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    standard_world().write(home)
    return home


def test_copy_store_copies_the_current_version_only(home, tmp_path, monkeypatch):
    (home / ".lock").write_text("")

    def refuse(*args, **kwargs):
        raise AssertionError("copy-store must not take the lock")

    monkeypatch.setattr(Store, "lock", refuse)
    copied = tool.copy_store(home, tmp_path / "copy")
    source, copy = Store(home), Store(tmp_path / "copy")
    assert copied == len([p for p in (tmp_path / "copy").rglob("*") if p.is_file()])
    assert copy.manifest() == source.manifest()
    for name in TABLES:
        assert copy.read_table(name).equals(source.read_table(name))
    assert not (tmp_path / "copy" / ".lock").exists() and not (tmp_path / "copy" / "raw").exists()


def test_a_dry_run_prints_the_report_and_writes_nothing(home, capsys):
    assert tool.main(["run", "--home", str(home), "--days", "2024-01-01..2024-01-31"]) == 0
    report = capsys.readouterr().out
    assert report.startswith("# BaoStock merge") and "dry run on version 1" in report
    assert "600010.SH | 2024-01-03 | 2024-01-04 | 1 | 1.25" in report  # the late seed event
    assert "**tradestatus_conflict:** 2 rows." in report and "| 600647.SH | 2024-01-03 | bar_kept |" in report
    assert Store(home).manifest()["version"] == 1 and not (home / "reports").exists()


def test_commit_writes_the_report_with_every_criterion(home):
    assert tool.main(["run", "--home", str(home), "--commit"]) == 0
    report = (home / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    for criterion in CRITERIA:
        assert f"| {criterion} | pass |" in report


def test_status_agrees_fails_when_a_bar_does_not_repeat_its_status(home):
    store = Store(home)
    plan = plan_merge(store, BaoStockRaw.at(home))
    apply_merge(store, plan)
    assert tool.status_agrees(store, store.manifest(), plan)[0]
    bar = store.read_table("bars", start=D2, end=D2, codes=["600000.SH"])
    store.commit({}, upsert={"bars": bar.with_columns(is_st=pl.lit(True))})
    ok, message = tool.status_agrees(store, store.manifest(), plan)
    assert not ok and "1 bars whose tradestatus or is_st differ" in message


def test_acceptance_catches_spans_bj_changes_and_stray_files(home):
    store = Store(home)
    plan = plan_merge(store, BaoStockRaw.at(home))
    apply_merge(store, plan)
    span = store.read_table("securities", codes=["600000.SH"]).with_columns(last_date=pl.lit(C2))
    bj_bar = store.read_table("bars", codes=["920001.BJ"], start=D1, end=D1).with_columns(close=pl.lit(1))
    store.commit({}, upsert={"securities": span, "bars": bj_bar})
    manifest = store.manifest()
    assert tool.invariant_problems(store, manifest) == ["1 SH/SZ spans differ from their bars: ['600000.SH']"]
    assert not tool.bj_frozen(store, store.manifest(plan.base_version), manifest)[0]
    stray = CommitResult(version=3, changed=True, files_written=["index_bars/index_bars.v3.parquet"])
    assert not tool.touched_only(plan, stray)[0]


def test_idempotency_ignores_raw_days_added_after_the_first_plan(home, monkeypatch):
    real = tool.plan_merge
    calls = []

    def plan_then_fetch(store, raw, **query):
        plan = real(store, raw, **query)
        calls.append(query)
        write_day(raw, D4, [day_row("600000.SH", D4, 10500, preclose=10400)])
        return plan

    monkeypatch.setattr(tool, "plan_merge", plan_then_fetch)
    assert tool.main(["run", "--home", str(home), "--commit", "--check-idempotency"]) == 0
    assert len(calls) == 2 and calls[0] == {"days": None}
    report = (home / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    assert "| idempotent | pass |" in report
    assert D4 not in Store(home).read_table("calendar")["date"]


def test_idempotency_needs_commit(home):
    with pytest.raises(SystemExit):
        tool.main(["run", "--home", str(home), "--check-idempotency"])
