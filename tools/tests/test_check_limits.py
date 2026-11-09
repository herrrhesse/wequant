import datetime as dt

import polars as pl
import pytest

import wequant.rules.tables as tables
from tests.frames import table_frame
from tools.check_limits import main
from wequant.data.schema import TABLES
from wequant.data.store import Store
from wequant.rules.check import KIND
from wequant.rules.limits import FREE, LIMITED, UNKNOWN
from wequant.rules.tables import check_store, limit_check_current, load_limits

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


def test_a_dry_run_reports_the_missing_breach_and_writes_nothing(store, capsys):
    before = files(store)
    assert main(["--home", str(store.home)]) == 0
    text = capsys.readouterr().out
    assert "- **Missing (found, not recorded):** 1\n" in text
    assert "| no rule | 0 |" in text
    assert files(store) == before and store.versions() == [1]


def test_a_commit_records_the_breach_and_a_current_check(store):
    assert main(["--home", str(store.home), "--commit"]) == 0
    manifest = store.manifest()
    assert store.versions() == [1, 2] and limit_check_current(manifest)
    text = (store.home / "reports" / "check_limits_2024-01-08_v2.md").read_text()
    assert "- **Missing (found, not recorded):** 1\n" in text and "v2 records every breach found" in text
    assert recorded(store).select("code", "date").rows() == [BREACH]
    assert check_store(store).missing.height == 0
    assert {k: manifest[k] for k in META} == META


def test_other_issue_kinds_are_untouched(store):
    before = store.read_table("data_issues")
    check_store(store, commit=True)
    assert store.read_table("data_issues").filter(pl.col("kind") != KIND).equals(before)


def test_an_unchanged_rerun_creates_no_version(store, monkeypatch):
    main(["--home", str(store.home), "--commit"])
    assert check_store(store, commit=True).committed is None
    monkeypatch.setattr(tables, "code_version", lambda: "0.0.0+gother")
    assert main(["--home", str(store.home), "--commit"]) == 0
    assert store.versions() == [1, 2]


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
