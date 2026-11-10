import datetime as dt
from importlib import resources

import polars as pl
import pytest

from tests.frames import table_frame
from wequant.data.schema import TABLES
from wequant.data.store import Store
from wequant.rules.book import load_book
from wequant.rules.check import KIND
from wequant.rules.limits import FREE, LIMITED, UNKNOWN
from wequant.rules.tables import check_store, limit_check_current, limit_inputs, load_limits

META = {"last_trade_date": "2024-01-08", "source": "test", "release_tag": "t1"}
DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
BREACH = ("600001.SH", dt.date(2024, 1, 4))


def bar(code: str, date: str, preclose: int | None, high: int = 10000, volume: int = 100) -> dict:
    """A bars row closing at 10.00 元."""
    return dict(code=code, date=date, open=10000, high=high, low=10000, close=10000, volume=volume, preclose=preclose)


def bars(rows: list[dict]) -> pl.DataFrame:
    return table_frame("bars", rows, amount=1000.0, preclose_derived=True, source="test")


def tables_v1() -> dict[str, pl.DataFrame]:
    rows = [bar("600000.SH", d, None if i == 0 else 10000) for i, d in enumerate(DAYS)]
    rows += [
        bar("600001.SH", d, None if i == 0 else 10000, high=11500 if i == 2 else 10000) for i, d in enumerate(DAYS[:3])
    ]
    rows += [bar("688001.SH", "2024-01-03", None), bar("688001.SH", "2024-01-04", 10000, high=20000)]
    securities = [
        {"code": "600000.SH", "exchange": "SH", "board": "main", "first_date": "2024-01-02", "last_date": "2024-01-08"},
        {"code": "600001.SH", "exchange": "SH", "board": "main", "first_date": "2024-01-02", "last_date": "2024-01-04"},
        {"code": "688001.SH", "exchange": "SH", "board": "star", "first_date": "2024-01-03", "last_date": "2024-01-04"},
    ]
    off_scale = [
        {"table": "bars", "code": "600000.SH", "date": "2023-12-29", "kind": "off_scale", "action": "row_dropped"}
    ]
    return {
        "bars": bars(rows),
        "securities": table_frame("securities", securities, source="test"),
        "calendar": table_frame("calendar", [{"date": d} for d in DAYS]),
        "adj_factor": table_frame("adj_factor", [{"code": "600000.SH", "ex_date": "2024-01-02", "factor": 1.0}]),
        "data_issues": table_frame("data_issues", off_scale, source="seed@t1"),
    }


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "home")
    store.commit(META, replace=tables_v1())
    return store


def files(store: Store) -> list[str]:
    return sorted(p.relative_to(store.home).as_posix() for p in store.home.rglob("*"))


def recorded(store: Store) -> pl.DataFrame:
    return store.read_table("data_issues").filter(pl.col("kind") == KIND)


def test_other_issue_kinds_are_untouched(store):
    before = store.read_table("data_issues")
    check_store(store, commit=True)
    assert store.read_table("data_issues").filter(pl.col("kind") != KIND).equals(before)


def test_new_inputs_make_the_check_stale_until_it_runs_again(store):
    check_store(store, commit=True)
    store.commit({}, upsert={"bars": bars([bar("600000.SH", "2024-01-08", 10000, volume=200)])})
    assert not limit_check_current(store.manifest())
    result = check_store(store, commit=True)
    assert result.missing.height == 0 and result.committed == 4
    assert limit_check_current(store.manifest())


def test_a_breach_that_disappears_is_stale_and_removed(store):
    check_store(store, commit=True)
    stored = store.read_table("bars", columns=list(TABLES["bars"].schema))
    kept = stored.filter((pl.col("code") != BREACH[0]) | (pl.col("date") != BREACH[1]))
    store.commit({}, replace={"bars": kept})
    assert check_store(store).stale.select("code", "date").rows() == [BREACH]
    check_store(store, commit=True)
    assert recorded(store).height == 0


def test_load_limits_applies_the_recorded_voids(store):
    check_store(store, commit=True)
    limits = load_limits(store)
    assert limits.columns == ["code", "date", "limit_up", "limit_down", "limit_state"]
    assert limits.height == store.read_table("bars", columns=["code"]).height
    by_key = {(c, d): (up, down, state) for c, d, up, down, state in limits.iter_rows()}
    assert by_key[BREACH] == (None, None, UNKNOWN)
    assert by_key[("600000.SH", dt.date(2024, 1, 3))] == (11000, 9000, LIMITED)
    assert by_key[("688001.SH", dt.date(2024, 1, 4))] == (None, None, FREE)


def test_a_rule_book_change_makes_the_check_stale(store, tmp_path):
    check_store(store, commit=True)
    edited = tmp_path / "rules.toml"
    edited.write_text(resources.files("wequant.rules").joinpath("rules.toml").read_text("utf-8") + "\n# edited\n")
    assert limit_check_current(store.manifest())
    assert not limit_check_current(store.manifest(), load_book(edited))


def test_st_and_adjustment_events_plug_in_through_daily_status_and_adj_factor(store):
    """600000.SH is suspended on 01-04, its ex-date, and ST from 01-05; its next event falls on Saturday 01-06."""
    stored = store.read_table("bars", columns=list(TABLES["bars"].schema))
    suspended = stored.filter((pl.col("code") != "600000.SH") | (pl.col("date") != dt.date(2024, 1, 4)))
    status = [
        {"code": "600000.SH", "date": d, "tradestatus": int(d != "2024-01-04"), "is_st": d >= "2024-01-05"}
        for d in DAYS
    ]
    events = [("600000.SH", "2024-01-02", 1.0), ("600000.SH", "2024-01-04", 2.0), ("600000.SH", "2024-01-06", 4.0)]
    adj = [{"code": code, "ex_date": day, "factor": factor} for code, day, factor in events]
    store.commit(
        {},
        replace={
            "bars": suspended,
            "daily_status": table_frame("daily_status", status, source="test"),
            "adj_factor": table_frame("adj_factor", adj),
        },
    )
    rows = limit_inputs(store).filter(pl.col("code") == "600000.SH").select("date", "is_st", "gap_days", "ex_date")
    assert rows.rows() == [
        (dt.date(2024, 1, 2), False, None, False),
        (dt.date(2024, 1, 3), False, 0, False),
        (dt.date(2024, 1, 5), True, 1, True),
        (dt.date(2024, 1, 8), True, 0, True),
    ]
    limits = load_limits(store).filter(pl.col("code") == "600000.SH")
    assert limits.select("limit_up", "limit_down").rows()[1:] == [(11000, 9000), (10500, 9500), (10500, 9500)]
