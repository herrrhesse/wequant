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
    empty_resolutions,
    standard_world,
    write_day,
)
from tests.frames import table_frame
from tools import baostock_merge as tool
from wequant.data.merge import apply_merge, plan_merge, resolutions
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


@pytest.fixture(autouse=True)
def no_decisions(tmp_path, monkeypatch):
    """The merge reads an empty resolutions file, never the user's packaged one."""
    path = empty_resolutions(tmp_path)
    monkeypatch.setattr(resolutions, "PACKAGED", path)
    monkeypatch.setattr(tool, "PACKAGED", path)


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
    report = capsys.readouterr().out
    assert report.startswith("# BaoStock merge") and "dry run on version 1" in report
    assert "0 applied, 0 closes flagged, 0 conflicts awaiting a decision" in report and "**Stop:**" not in report
    assert (
        "- **turn:** 3 of 26 merged BaoStock bars have a turnover (11.5385%); 23 merged bars trade without one."
        in report
    )
    assert "stale layouts" not in report and "- **Stale layouts rewritten:** none; 0 of these years (0 rows)" in report
    assert "600010.SH | 2024-01-03 | 2024-01-04 | 1 | 1.25" in report  # the late seed event
    assert "**tradestatus_conflict:** 2 rows." in report and "| 600647.SH | 2024-01-03 | bar_kept |" in report
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


def test_propose_adds_entries_and_keeps_existing_ones(conflicts, tmp_path, capsys):
    path = tmp_path / "r.toml"
    assert tool.main(["propose", "--home", str(conflicts), "--out", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"5 proposals; 5 added to {path}, 0 already there",
        "  average_price take null [amount]: 1",
        "  average_price take seed [amount]: 1",
        "  average_price take seed [volume, amount]: 1",
        "  price_mismatch take baostock [open, high, low, close]: 1",
        "  price_mismatch take seed [open, high, low, close]: 1",
    ]
    text = path.read_text("utf-8")
    assert tool.main(["propose", "--home", str(conflicts), "--out", str(path)]) == 0
    assert "0 added" in capsys.readouterr().out and path.read_text("utf-8") == text
    assert Store(conflicts).manifest()["version"] == 1


def test_proposed_entries_stop_the_run_and_approved_ones_merge(conflicts, tmp_path, capsys):
    path = tmp_path / "r.toml"
    tool.main(["propose", "--home", str(conflicts), "--out", str(path)])
    run = ["run", "--home", str(conflicts), "--resolutions", str(path), "--commit"]
    capsys.readouterr()
    assert tool.main(run) == 1
    report = capsys.readouterr().out
    assert report.splitlines()[2] == (
        "- **Stop:** 5 conflicts lack an approved resolution (0 without an entry, 0 waiting for their reference day, "
        "5 with a proposed entry awaiting approval, 0 amount conflicts waiting for their cell's price entry); nothing "
        "is committed until each has one (section 5)."
    )
    assert (
        "| 600002.SH | 2024-01-03 | average_price | a proposed entry awaits approval | open 10300, high 10300, "
        in report
    )
    assert Store(conflicts).manifest()["version"] == 1 and not (conflicts / "reports").exists()

    path.write_text(path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"'), "utf-8")
    assert tool.main(run) == 0
    report = (conflicts / "reports" / f"baostock_merge_{D3}_v2.md").read_text("utf-8")
    assert "5 applied, 1 closes flagged, 0 conflicts awaiting a decision, 0 approved entries met no conflict" in report
    row = (
        "| 600003.SH | 2024-01-04 | average_price | resolved_null | amount | no store bar | amount 0.0 | amount None |"
    )
    assert row in report
    assert "| 600004.SH | 2024-01-02 | 10,210 | 10,210 | 10,200 | 2024-01-03 | 10,220 | False |" in report
    assert "| resolutions_applied | pass | 5 resolutions applied; committed bars differing" in report
    assert "## 5. Conflicts awaiting a decision\n\nNone." in report


def test_the_report_lists_conflicts_without_an_entry_first_and_idle_entries(conflicts, tmp_path, capsys):
    path = tmp_path / "r.toml"
    tool.main(["propose", "--home", str(conflicts), "--out", str(path)])
    text = path.read_text("utf-8")
    last = text.rindex("[[resolution]]")  # 600003.SH's entry
    path.write_text(text[:last], "utf-8")
    run = ["run", "--home", str(conflicts), "--resolutions", str(path)]
    capsys.readouterr()
    assert tool.main(run) == 1
    report = capsys.readouterr().out
    assert "(1 without an entry, 0 waiting for their reference day, 4 with a proposed entry" in report
    listed = [line.split(" | ")[0][2:] for line in report.split("## 5.")[1].splitlines() if line.startswith("| 6")]
    assert listed == ["600003.SH", "600000.SH", "600004.SH", "600001.SH", "600002.SH"]

    tool.main(["propose", "--home", str(conflicts), "--out", str(path)])
    path.write_text(path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"'), "utf-8")
    assert tool.main([*run, "--commit"]) == 0
    path.write_text(path.read_text("utf-8").replace('take = "baostock"', 'take = "seed"'), "utf-8")
    capsys.readouterr()
    assert tool.main(run) == 0
    report = capsys.readouterr().out
    assert "0 conflicts awaiting a decision, 1 approved entries met no conflict" in report
    assert "| 600004.SH | 2024-01-02 | price_mismatch | open, high, low, close | seed |" in report


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
    bar = store.read_table("bars", start=D2, end=D2, codes=["600000.SH"])
    store.commit({}, upsert={"bars": bar.with_columns(is_st=pl.lit(True))})
    ok, message = tool.status_agrees(store, store.manifest(), plan)
    assert not ok and "1 bars whose tradestatus or is_st differ" in message


def test_status_agrees_fails_without_a_conflict_issue_or_a_bar_s_status(merged):
    store, plan, _ = merged
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


def test_adjustment_checked_fails_when_a_bar_was_not_checked(merged):
    store, plan, _ = merged
    assert tool.adjustment_checked(store, store.manifest(), plan)[0]
    stats = {
        **plan.stats,
        "adjustment": {**plan.stats["adjustment"], "checked": plan.stats["adjustment"]["checked"] - 1},
    }
    assert not tool.adjustment_checked(store, store.manifest(), dataclasses.replace(plan, stats=stats))[0]


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


def test_status_complete_fails_for_a_dropped_status_row_now_after_the_first_bar(merged):
    store, _, _ = merged
    assert tool.status_complete(store, store.manifest())[0]
    dropped = {"table": "daily_status", "code": "600000.SH", "date": C2, "kind": "before_first_bar"}  # first bar C1
    store.commit(
        {}, upsert={"data_issues": table_frame("data_issues", [dropped], action="status_dropped", source=SOURCE)}
    )
    ok, message = tool.status_complete(store, store.manifest())
    assert not ok and message.endswith("re-merge 1 days: 2023-12-29")


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


def test_idempotency_needs_commit(home):
    with pytest.raises(SystemExit):
        tool.main(["run", "--home", str(home), "--check-idempotency"])
