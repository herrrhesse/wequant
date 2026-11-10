import datetime as dt
import json

import polars as pl
import pytest

from tests.baostock_raw_fake import (
    C1,
    C2,
    CALENDAR,
    D1,
    D2,
    D3,
    D4,
    LIMIT_BREACH,
    MERGED,
    SOURCE,
    World,
    day_row,
    empty_resolutions,
    stale_bars,
    standard_world,
    suspended_row,
    write_day,
    write_factors,
)
from tests.frames import table_frame
from tools.baostock_merge import invariant_problems, status_complete
from wequant.data.merge import RawDataError, StalePlan, apply_merge, plan_merge, resolutions
from wequant.data.merge.assemble import stale_years
from wequant.data.schema import BARS
from wequant.version import code_version

FIXED_600010 = [(dt.date(1999, 1, 4), 1.0), (D2, 1.25)]


@pytest.fixture(autouse=True)
def no_decisions(tmp_path, monkeypatch):
    """The merge reads an empty resolutions file, never the user's packaged one."""
    monkeypatch.setattr(resolutions, "PACKAGED", empty_resolutions(tmp_path))


@pytest.fixture
def world(tmp_path):
    return standard_world().write(tmp_path)


def merge(store, raw, **query):
    plan = plan_merge(store, raw, **query)
    return plan, apply_merge(store, plan)


def rows_of(store, table, code, columns):
    return store.read_table(table, codes=[code]).select(columns).rows()


def issue_keys(issues, kind):
    return set(issues.filter(pl.col("kind") == kind).select("code", "date", "action").rows())


def parts(paths):
    return {p.rsplit(".v", 1)[0] for p in paths}


def test_days_must_be_on_disk_and_cover_the_calendar(world):
    store, raw = world
    with pytest.raises(ValueError, match="skip 1 store calendar days: 2024-01-03"):
        plan_merge(store, raw, days=[D1, D3])
    with pytest.raises(ValueError, match="no raw day"):
        plan_merge(store, raw, days=[])
    with pytest.raises(ValueError, match="no raw snapshot for 1 days: 2024-01-05"):
        plan_merge(store, raw, days=[D4])
    with pytest.raises(ValueError, match="SH/SZ"):
        plan_merge(store, raw, factor_codes=["920001.BJ"])


def test_empty_factor_codes_read_no_factor_file(world):
    store, raw = world
    write_factors(raw, "600000.SH", [(D1, 0.0)])
    assert plan_merge(store, raw, factor_codes=()).factor_codes == []
    with pytest.raises(RawDataError, match="factor"):
        plan_merge(store, raw)


def test_end_to_end(world):
    store, raw = world
    before = {t: store.read_table(t) for t in ("bars", "securities", "adj_factor", "calendar")}
    plan, result = merge(store, raw)
    assert result.changed and store.manifest()["version"] == 2

    bj = pl.col("code") == "920001.BJ"
    for table in ("bars", "securities", "adj_factor"):
        assert store.read_table(table).filter(bj).equals(before[table].filter(bj))
    assert store.read_table("daily_status").filter(bj).is_empty()

    securities = store.read_table("securities")
    assert securities.filter(pl.col("code") == "600849.SH").row(0) == ("600849.SH", "SH", "main", D1, D3, None, SOURCE)
    assert rows_of(store, "adj_factor", "600849.SH", ["ex_date"]) == []
    assert rows_of(store, "adj_factor", "600006.SH", ["ex_date", "factor"]) == [(C1, 3.7), (D2, 3.8)]

    assert "600008.SH" not in securities["code"]
    assert issue_keys(plan.issues, "no_bars") == {("600008.SH", D3, "security_removed")}
    assert plan.stats["adj_factor"] == {"rewritten": True, "removed": ["600008.SH"], "moved": ["600009.SH"]}
    assert rows_of(store, "adj_factor", "600008.SH", ["ex_date"]) == []

    assert json.loads(plan.issues.filter(pl.col("code") == "600009.SH")["detail"].item()) == {
        "position": "before_first"
    }
    assert rows_of(store, "adj_factor", "600009.SH", ["ex_date", "factor"]) == [(D1, 2.0)]

    merged = store.read_table("bars", start=D1, end=D3).filter(~bj)
    assert set(merged["source"]) == {SOURCE} and not merged["preclose_derived"].any()
    assert merged.filter(pl.col("turn").is_not_null()).select("code", "turn").rows() == [("600000.SH", 0.5123)] * 3
    assert plan.stats["turn"] == {"bars": 26, "non_null": 3, "null_traded": 23}
    assert store.read_table("bars", end=C2)["turn"].is_null().all()  # seed rows have none
    status = store.read_table("daily_status").select(
        "code", "date", st_status=pl.col("tradestatus"), st=pl.col("is_st")
    )
    joined = merged.join(status, on=["code", "date"], how="left")
    assert joined["tradestatus"].equals(joined["st_status"], check_names=False)
    assert joined["is_st"].equals(joined["st"], check_names=False)

    assert store.read_table("calendar").equals(before["calendar"])
    assert invariant_problems(store, store.manifest()) == []
    issues = store.read_table("data_issues")
    assert {"limit_breach", "off_scale"} <= set(issues["kind"])
    assert plan.stats["adjustment"]["first_bar"] == 3 and plan.stats["adjustment"]["checked"] == 23
    assert plan.stats["rows"] == {table: store.read_table(table).height for table in plan.stats["rows"]}


def test_unchanged_factor_rows_are_not_rewritten(tmp_path):
    w = World()
    w.stock("600006.SH", {C1: 3700, C2: 3750, D1: 3800, D2: 3720, D3: 3730}, steps={D2: 3.8 / 3.7}, factor=3.7)
    store, raw = w.write(tmp_path)
    plan = plan_merge(store, raw)
    assert "adj_factor" not in plan.replace and plan.issues.is_empty()
    assert plan.stats["adj_factor"] == {"rewritten": False, "removed": [], "moved": []}


def test_duplicate_codes(world):
    store, raw = world
    plan, _ = merge(store, raw)
    codes = pl.col("code").is_in(["300114.SZ", "302132.SZ"])
    assert store.read_table("bars").filter(codes).select("code", "date").rows() == [
        ("300114.SZ", C1),
        ("300114.SZ", C2),
        ("300114.SZ", D1),
        ("300114.SZ", D2),
        ("302132.SZ", D3),
    ]
    duplicates = plan.issues.filter(pl.col("kind") == "duplicate_code")
    assert issue_keys(duplicates, "duplicate_code") == {("302132.SZ", d, "duplicate_dropped") for d in (D1, D2)}
    assert {json.loads(x)["same_as"] for x in duplicates["detail"]} == {"300114.SZ"}
    assert plan.issues.filter(codes & (pl.col("kind") != "duplicate_code")).is_empty()
    status = store.read_table("daily_status").filter(codes)
    assert status.select("code", "date", "tradestatus").rows() == [
        ("300114.SZ", D1, 1),
        ("300114.SZ", D2, 1),
        ("300114.SZ", D3, 0),
        ("302132.SZ", D3, 1),
    ]
    securities = store.read_table("securities").filter(codes)
    assert securities.select("code", "first_date", "last_date").rows() == [("300114.SZ", C1, D2), ("302132.SZ", D3, D3)]


def test_status_conflicts_are_exactly_the_fixture_s_two(world):
    store, raw = world
    plan = plan_merge(store, raw)
    conflicts = plan.issues.filter(pl.col("kind") == "tradestatus_conflict")
    assert set(conflicts.select("code", "date", "action").rows()) == {
        ("600647.SH", D2, "bar_kept"),
        ("600766.SH", D3, "no_bar"),
    }
    assert set(conflicts["table"]) == {"daily_status"}


def test_status_before_the_first_bar_is_dropped_not_a_conflict(tmp_path):
    w = World()
    w.stock("688001.SH", {D2: 5000, D3: 5100}, seed=(), raw=(D2, D3))
    w.day(D1, day_row("688001.SH", D1, 5000, volume="0", amount="0.0000"))
    store, raw = w.write(tmp_path)
    plan, _ = merge(store, raw)
    assert set(plan.issues.select("kind", "date").rows()) == {
        ("before_first_bar", D1),
        ("bar_added", D2),
        ("bar_added", D3),
    }
    assert rows_of(store, "daily_status", "688001.SH", ["date"]) == [(D2,), (D3,)]


def test_merging_older_days_later_keeps_status_after_each_first_bar(tmp_path):
    w = World()
    steady = {C1: 10000, C2: 10100, D1: 10200, D2: 10300, D3: 10400}
    w.stock("600000.SH", steady, raw=CALENDAR)
    w.stock("600100.SH", steady, raw=(D2, D3))  # BaoStock omits C1 and C2: its first bar moves to D2
    w.stock("600200.SH", {C1: 5000, C2: 5000}, raw=())  # BaoStock omits C1 and C2: no bar is left
    w.stock("600300.SH", {C2: 7000, D1: 7000, D2: 7100, D3: 7200}, seed=(D2, D3), raw=(C2, D2, D3))
    w.day(D1, suspended_row("600100.SH", D1), suspended_row("600200.SH", D1), suspended_row("600300.SH", D1))
    for day in (D2, D3):
        w.day(day, suspended_row("600200.SH", day))
    store, raw = w.write(tmp_path)
    first, _ = merge(store, raw, days=MERGED)
    assert issue_keys(first.issues, "before_first_bar") == {("600300.SH", D1, "status_dropped")}

    older, result = merge(store, raw, days=[C1, C2])
    assert older.stats["years"]["daily_status"] == [2023, 2024] and "daily_status/2024" in parts(result.files_written)
    dropped = {("600100.SH", D1, "status_dropped")} | {("600200.SH", day, "status_dropped") for day in MERGED}
    assert issue_keys(older.issues, "before_first_bar") == dropped
    assert invariant_problems(store, store.manifest()) == []
    assert older.stats["status_to_restore"] == {"rows": 1, "days": [D1]}
    ok, message = status_complete(store, store.manifest())
    assert not ok and message.endswith("re-merge 1 days: 2024-01-02")

    merge(store, raw, days=[D1])
    assert rows_of(store, "daily_status", "600300.SH", ["date", "tradestatus"]) == [(C2, 1), (D1, 0), (D2, 1), (D3, 1)]
    assert status_complete(store, store.manifest())[0]


def test_a_merge_that_would_empty_a_stored_year_is_refused(tmp_path):
    w = World()
    w.stock("600000.SH", {C1: 10000, C2: 10100}, raw=(C1, C2))
    w.stock("600200.SH", {C1: 5000, C2: 5000}, raw=())
    for day in MERGED:
        w.day(day, suspended_row("600200.SH", day))
    store, raw = w.write(tmp_path)
    merge(store, raw, days=MERGED)
    with pytest.raises(ValueError, match=r"no daily_status rows in \[2024\]"):
        plan_merge(store, raw, days=[C1, C2])


def test_flagged_rows_are_state_replaced_on_every_merge(world):
    store, raw = world
    plan, _ = merge(store, raw)
    assert issue_keys(plan.issues, "event_missing") == {("600010.SH", D2, "flagged")}
    assert issue_keys(plan.issues, "event_unimplied") == {("600010.SH", D3, "flagged")}
    assert issue_keys(plan.issues, "event_step_off") == {("600011.SH", D2, "flagged")}
    late = plan.stats["adjustment"]["late_seed_events"]
    assert late == [{"code": "600010.SH", "missing": D2, "unimplied": D3, "lag": 1, "step": 1.25}]

    write_factors(raw, "600010.SH", FIXED_600010)
    plan, _ = merge(store, raw)
    assert issue_keys(plan.issues, "event_added") == {("600010.SH", D2, "event_added")}
    assert issue_keys(plan.issues, "event_unconfirmed") == {("600010.SH", D3, "event_dropped")}
    assert rows_of(store, "adj_factor", "600010.SH", ["ex_date", "factor"]) == [(C1, 1.0), (D2, 1.25)]
    issues = store.read_table("data_issues")
    flagged = issues.filter(pl.col("action") == "flagged").select("code", "date", "kind").rows()
    assert flagged == [("600011.SH", D2, "event_step_off")]
    assert issues.filter(pl.col("kind") == "limit_breach").select(list(LIMIT_BREACH)).row(0, named=True) == LIMIT_BREACH


def test_the_previous_close_comes_from_the_store(world):
    store, raw = world
    plan = plan_merge(store, raw, days=[D2, D3])
    missing = plan.issues.filter(pl.col("kind") == "event_missing")
    assert json.loads(missing["detail"].item())["prev_date"] == "2024-01-02"
    assert json.loads(missing["detail"].item())["prev_close"] == 10000


def test_partitions_and_migration(tmp_path):
    w = standard_world()
    w.factors = {"600010.SH": FIXED_600010, "600000.SH": [(dt.date(1999, 1, 4), 1.0)]}
    store, raw = w.write(tmp_path)
    stale_bars(store)
    plan, first = merge(store, raw)
    assert plan.stats["years"] == {"bars": [2023, 2024], "daily_status": [2024], "stale": [2023, 2024]}
    assert plan.stats["layout_only"] == {"years": [2023], "rows": 16}  # 8 codes on 2023-12-28 and 29, no merged day
    expected = {"bars/2023", "bars/2024", "daily_status/2024", "adj_factor/adj_factor"}
    assert parts(first.files_written) == expected | {"securities/securities", "data_issues/data_issues"}
    assert parts(first.files_reused) == {"calendar/calendar"}
    assert {"index_bars/index_bars", "index_members/index_members"} <= parts(first.files_kept)
    assert stale_years(store, store.manifest(), "bars") == set()
    assert (
        dict(pl.read_parquet_schema(tmp_path / store.manifest()["tables"]["bars"]["files"][0]["path"])) == BARS.schema
    )

    _, again = merge(store, raw)
    assert not again.changed and again.files_written == []

    write_day(raw, D4, [day_row("600010.SH", D4, 6000, preclose=6400)])
    plan, last = merge(store, raw, days=[D4], factor_codes=())
    assert parts(last.files_written) == {
        "bars/2024",
        "daily_status/2024",
        "calendar/calendar",
        "securities/securities",
        "data_issues/data_issues",
    }
    assert {"bars/2023", "adj_factor/adj_factor", "index_bars/index_bars", "index_members/index_members"} <= parts(
        last.files_kept
    )
    assert set(plan.issues["kind"]) == {"event_missing", "event_step_off"}
    assert issue_keys(plan.issues, "event_missing") == {("600010.SH", D4, "flagged")}
    assert rows_of(store, "securities", "600010.SH", ["last_date"]) == [(D4,)]
    assert store.read_table("calendar")["date"].to_list() == [*CALENDAR, D4]


def test_a_stale_plan_is_refused(world):
    store, raw = world
    plan = plan_merge(store, raw)
    store.commit({}, upsert={"calendar": table_frame("calendar", [{"date": C1}, {"date": D4}])})
    with pytest.raises(StalePlan):
        apply_merge(store, plan)
    assert store.manifest()["version"] == 2


def test_meta_describes_the_status_coverage_and_never_moves_back(tmp_path):
    w = World()
    w.stock("600000.SH", {C1: 10000, C2: 10100, D1: 10200, D2: 10300, D3: 10400}, raw=(C2, *MERGED))
    store, raw = w.write(tmp_path)
    merge(store, raw)
    manifest = store.manifest()
    coverage = {"first_day": "2023-12-29", "last_day": "2024-01-04", "days": 4}
    assert manifest["baostock_merge"] == coverage | {"code_version": code_version()}
    assert manifest["last_trade_date"] == "2024-01-04"
    assert (manifest["source"], manifest["release_tag"]) == ("seed", "t")
    plan, result = merge(store, raw, days=[D1])
    assert plan.stats["years"]["daily_status"] == [2024]  # 2023 comes from the stored part
    assert plan.meta == {"last_trade_date": "2024-01-04", "baostock_merge": coverage}
    assert not result.changed
