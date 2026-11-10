import polars as pl
import pytest

import wequant.rules.tables as tables
from tests.test_rules_limits import inputs
from tests.test_rules_tables import BREACH, META, files, recorded
from wequant.data.schema import TABLES
from wequant.data.store import Store
from wequant.rules.book import default_book
from wequant.rules.limits import price_limits
from wequant.rules.tables import check_store
from wequant.service import CheckRequest, Context, Exit, ServiceError, data_check
from wequant.service.limit_report import guard_rows


def check(home, commit: bool = False):
    return data_check(CheckRequest(commit=commit), Context(home))


def test_a_dry_run_reports_the_missing_breach_and_writes_nothing(limits_home):
    store, before = Store(limits_home), files(Store(limits_home))
    result = check(limits_home)
    assert not result.ok and (result.data_version, result.committed, result.current) == (1, None, False)
    assert result.rules == default_book().digest
    assert (result.bars, result.breaches, result.missing, result.stale, result.report_path) == (10, 1, 1, 0, None)
    assert "- **Mode:** dry run; nothing written" in result.report
    assert "- **Missing (found, not recorded):** 1\n" in result.report
    assert "| no rule | 0 |" in result.report and "other cannot occur" in result.report
    assert files(store) == before and store.versions() == [1]


def test_a_commit_records_the_breach_and_a_current_check(limits_home):
    store = Store(limits_home)
    result = check(limits_home, commit=True)
    assert result.ok and (result.committed, result.current, result.missing) == (2, True, 1)
    assert result.report_path == "reports/check_limits_2024-01-08_v2.md"
    assert (limits_home / result.report_path).read_text("utf-8") == result.report
    assert "v2 records every breach found" in result.report
    assert "| LIMITED | 5 |" in result.report and "| UNKNOWN | 3 |" in result.report
    assert "| no preclose | 2 |" in result.report and "| voided | 1 |" in result.report
    assert recorded(store).select("code", "date").rows() == [BREACH]
    assert check_store(store).missing.height == 0
    assert {k: store.manifest()[k] for k in META} == META


def test_an_unchanged_rerun_creates_no_version(limits_home, monkeypatch):
    first = check(limits_home, commit=True)
    monkeypatch.setattr(tables, "code_version", lambda: "0.0.0+gother")
    again = check(limits_home, commit=True)
    assert again.ok and (again.committed, again.data_version, again.missing) == (None, 2, 0)
    assert again.report_path == first.report_path and Store(limits_home).versions() == [1, 2]
    assert check(limits_home).ok


def test_a_breach_that_disappears_is_stale(limits_home):
    check(limits_home, commit=True)
    store = Store(limits_home)
    stored = store.read_table("bars", columns=list(TABLES["bars"].schema))
    store.commit({}, replace={"bars": stored.filter((pl.col("code") != BREACH[0]) | (pl.col("date") != BREACH[1]))})
    result = check(limits_home)
    assert not result.ok and (result.stale, result.missing) == (1, 0)


def test_a_locked_store_refuses_a_commit_but_not_a_dry_run(limits_home):
    with Store(limits_home).lock():
        with pytest.raises(ServiceError, match="is held by") as raised:
            check(limits_home, commit=True)
        assert raised.value.exit == Exit.LOCKED
        assert check(limits_home).data_version == 1
    assert Store(limits_home).versions() == [1]


def test_no_store(tmp_path):
    with pytest.raises(ServiceError, match="no store at") as raised:
        check(tmp_path / "empty")
    assert raised.value.exit == Exit.NOT_FOUND


def test_guard_rows_find_every_limit_a_tick_guard_moved():
    frame = price_limits(
        inputs(
            [
                {"code": "600000.SH", "date": "2024-02-20", "board": "main", "preclose": 100, "is_st": True},
                {"code": "600001.SH", "date": "2024-02-20", "board": "main", "preclose": 50},
                {"code": "600002.SH", "date": "2024-02-20", "board": "main", "preclose": 200, "is_st": True},
            ]
        )
    )
    assert frame.select("limit_up", "limit_down").rows() == [(110, 90), (60, 40), (210, 190)]
    assert guard_rows(frame, default_book())[0] == "Bars where a tick guard moved a limit: 2."
