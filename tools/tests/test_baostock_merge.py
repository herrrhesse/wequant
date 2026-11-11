import dataclasses

import polars as pl
import pytest

from tests.baostock_raw_fake import (
    C2,
    CONFLICTS,
    D1,
    D2,
    D3,
    D4,
    SOURCE,
    conflict_world,
    day_row,
    standard_world,
    write_day,
)
from tests.frames import table_frame
from tools import baostock_merge as tool
from wequant.data.merge import apply_merge, plan_merge
from wequant.data.merge.assemble import BJ
from wequant.data.raw import BaoStockRaw
from wequant.data.schema import TABLES
from wequant.data.store import Store

CRITERIA = (
    "resolutions_applied",
    "status_agrees",
    "adjustment_checked",
    "factor_files_consistent",
    "touched_only",
    "registered",
    "status_complete",
    "bj_frozen",
)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    standard_world().write(home)
    return home


@pytest.fixture
def merged(home):
    """(store, plan, commit) of the standard world merged."""
    store = Store(home)
    plan = plan_merge(store, BaoStockRaw.at(home))
    return store, plan, apply_merge(store, plan)


def status_row(code, day, tradestatus=1):
    return table_frame(
        "daily_status", [{"code": code, "date": day}], tradestatus=tradestatus, is_st=False, source=SOURCE
    )


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
    assert capsys.readouterr().out.startswith("# BaoStock merge")
    assert Store(home).manifest()["version"] == 1 and not (home / "reports").exists()


def test_commit_writes_the_report_with_every_criterion(home):
    assert tool.main(["run", "--home", str(home), "--commit"]) == 0
    report = (home / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    for criterion in CRITERIA:
        assert f"| {criterion} | pass |" in report


@pytest.fixture
def conflicts(tmp_path):
    home = tmp_path / "conflicts"
    conflict_world().write(home)
    return home


def test_proposed_entries_stop_the_run_and_approved_ones_merge(conflicts, tmp_path, capsys):
    path = tmp_path / "r.toml"
    tool.main(["propose", "--home", str(conflicts), "--out", str(path)])
    run = ["run", "--home", str(conflicts), "--resolutions", str(path), "--commit"]
    capsys.readouterr()
    assert tool.main(run) == 1  # proposed entries: nothing is committed
    assert Store(conflicts).manifest()["version"] == 1 and not (conflicts / "reports").exists()

    path.write_text(path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"'), "utf-8")
    assert tool.main(run) == 0
    assert Store(conflicts).manifest()["version"] == 2
    report = (conflicts / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    for criterion in CRITERIA:
        assert f"| {criterion} | pass |" in report


def test_resolutions_applied_fails_when_a_committed_bar_differs(conflicts, tmp_path):
    path = tmp_path / "r.toml"
    tool.main(["propose", "--home", str(conflicts), "--out", str(path)])
    path.write_text(path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"'), "utf-8")
    store = Store(conflicts)
    plan = plan_merge(store, BaoStockRaw.at(conflicts), resolutions=path)
    apply_merge(store, plan)
    assert tool.resolutions_applied(store, store.manifest(), plan)[0]
    code, day, _ = CONFLICTS[4]
    bar = store.read_table("bars", start=day, end=day, codes=[code]).with_columns(amount=pl.lit(0.0))
    store.commit({}, upsert={"bars": bar})
    ok, message = tool.resolutions_applied(store, store.manifest(), plan)
    assert not ok and message.endswith("committed bars differing from their after values: 600003.SH 2024-01-04")


def test_status_agrees_fails_when_a_bar_does_not_repeat_its_status(merged):
    store, plan, _ = merged
    assert tool.status_agrees(store, store.manifest(), plan)[0]
    bars = store.read_table("bars", start=D2, end=D2, codes=["600000.SH"])
    store.commit({}, upsert={"bars": bars.with_columns(is_st=pl.lit(True))})
    ok, message = tool.status_agrees(store, store.manifest(), plan)
    assert not ok and "1 bars whose tradestatus or is_st differ" in message
    store.commit({}, upsert={"bars": bars})

    issues = store.read_table("data_issues")
    store.commit({}, replace={"data_issues": issues.filter(pl.col("kind") != "tradestatus_conflict")})
    ok, message = tool.status_agrees(store, store.manifest(), plan)
    assert not ok and "2 disagreements, 0 tradestatus_conflict issues" in message

    store.commit({}, replace={"data_issues": issues})
    status = store.read_table("daily_status")
    lost = (pl.col("code") == "600647.SH") & (pl.col("date") == D2)  # its bar_kept conflict stays recorded
    store.commit({}, replace_parts={"daily_status": status.filter(~lost)})
    ok, message = tool.status_agrees(store, store.manifest(), plan)
    assert not ok and "1 bars without status" in message


def test_registered_catches_spans_unknown_codes_and_early_status(merged):
    store, _, _ = merged
    span = store.read_table("securities", codes=["600000.SH"]).with_columns(last_date=pl.lit(C2))
    store.commit({}, upsert={"securities": span})
    assert tool.invariant_problems(store, store.manifest()) == ["1 SH/SZ spans differ from their bars: ['600000.SH']"]

    store.commit({}, upsert={"securities": span.with_columns(last_date=pl.lit(D3))})
    store.commit(
        {}, upsert={"adj_factor": table_frame("adj_factor", [{"code": "600999.SH", "ex_date": D1, "factor": 1.0}])}
    )
    assert tool.invariant_problems(store, store.manifest()) == [
        "adj_factor has 1 codes without a securities row: ['600999.SH']"
    ]

    store.commit({}, replace={"adj_factor": store.read_table("adj_factor").filter(pl.col("code") != "600999.SH")})
    store.commit({}, upsert={"daily_status": status_row("600849.SH", C2)})  # its first bar is on D1
    assert tool.invariant_problems(store, store.manifest()) == [
        "1 daily_status rows are BJ or before the code's first bar"
    ]


def test_bj_frozen_catches_bars_and_status_rows(merged):
    store, plan, _ = merged
    base = store.manifest(plan.base_version)
    assert tool.bj_frozen(store, base, store.manifest())[0]
    store.commit({}, upsert={"daily_status": status_row("920001.BJ", D1)})
    ok, message = tool.bj_frozen(store, base, store.manifest())
    assert not ok and message.endswith("1 BJ status rows")

    store.commit({}, replace_parts={"daily_status": store.read_table("daily_status").filter(~BJ)})
    assert tool.bj_frozen(store, base, store.manifest())[0]
    bj_bar = store.read_table("bars", codes=["920001.BJ"], start=D1, end=D1).with_columns(close=pl.lit(1))
    store.commit({}, upsert={"bars": bj_bar})
    assert not tool.bj_frozen(store, base, store.manifest())[0]


def test_touched_only_needs_every_other_part_kept(merged):
    store, plan, commit = merged
    manifest = store.manifest()
    assert tool.touched_only(plan, commit, manifest)[0]
    stray = dataclasses.replace(commit, files_written=[*commit.files_written, "index_bars/index_bars.v2.parquet"])
    assert not tool.touched_only(plan, stray, manifest)[0]
    for part in ("bars/2023", "index_bars/index_bars"):
        lost = dataclasses.replace(commit, files_kept=[p for p in commit.files_kept if not p.startswith(part)])
        ok, message = tool.touched_only(plan, lost, manifest)
        assert not ok and message.endswith(f"other parts not kept: ['{part}']")


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
    assert len(calls) == 2 and calls[0] == {"days": None, "resolutions": None}
    report = (home / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    assert "| idempotent | pass |" in report
    assert D4 not in Store(home).read_table("calendar")["date"]
