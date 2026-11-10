import pytest

import wequant.rules.tables as tables
from tests.test_rules_tables import BREACH, META, files, recorded
from wequant.data.store import Store
from wequant.rules.book import default_book
from wequant.rules.tables import check_store
from wequant.service import CheckRequest, Context, Exit, ServiceError, data_check


def check(home, commit: bool = False):
    return data_check(CheckRequest(commit=commit), Context(home))


def test_a_dry_run_reports_the_missing_breach_and_writes_nothing(limits_home):
    """data check is read-only without --commit: it reports the unrecorded breach and fails, writing no file."""
    store, before = Store(limits_home), files(Store(limits_home))
    result = check(limits_home)
    assert not result.ok and (result.data_version, result.committed, result.current) == (1, None, False)
    assert result.rules == default_book().digest
    assert (result.bars, result.breaches, result.missing, result.stale, result.report_path) == (10, 1, 1, 0, None)
    assert files(store) == before and store.versions() == [1]


def test_a_commit_records_the_breach_and_a_current_check(limits_home):
    """--commit records the breach and a current check in a new version and writes the report it returns."""
    store = Store(limits_home)
    result = check(limits_home, commit=True)
    assert result.ok and (result.committed, result.current, result.missing) == (2, True, 1)
    assert result.report_path == "reports/check_limits_2024-01-08_v2.md"
    assert (limits_home / result.report_path).read_text("utf-8") == result.report
    assert recorded(store).select("code", "date").rows() == [BREACH]
    assert check_store(store).missing.height == 0
    assert {k: store.manifest()[k] for k in META} == META


def test_an_unchanged_rerun_creates_no_version(limits_home, monkeypatch):
    """A rerun with the same inputs and rules, even from another code version, adds no store version."""
    first = check(limits_home, commit=True)
    monkeypatch.setattr(tables, "code_version", lambda: "0.0.0+gother")
    again = check(limits_home, commit=True)
    assert again.ok and (again.committed, again.data_version, again.missing) == (None, 2, 0)
    assert again.report_path == first.report_path and Store(limits_home).versions() == [1, 2]
    assert check(limits_home).ok


def test_a_locked_store_refuses_a_commit_but_not_a_dry_run(limits_home):
    """LOCKED (exit 5) while another writer holds the store lock; a dry run still reads."""
    with Store(limits_home).lock():
        with pytest.raises(ServiceError, match="is held by") as raised:
            check(limits_home, commit=True)
        assert raised.value.exit == Exit.LOCKED
        assert check(limits_home).data_version == 1
    assert Store(limits_home).versions() == [1]
