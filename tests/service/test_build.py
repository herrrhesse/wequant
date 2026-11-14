"""The first build, the history behind it and the user's own files, through the service (round 3, S2.9); BaoStock is
tests.test_build's fake."""

import pytest

from tests.test_build import NOW, market
from wequant import service
from wequant.data.store import Store
from wequant.service import Context, Exit, ServiceError

BARS = (
    "code,date,open,high,low,close,volume,amount\n"
    "600000.SH,2024-01-02,10,10.5,9.8,10.2,1000,10200\n"
    "600000.SH,2024-01-03,10.2,10.6,10.1,10.4,1100,11440\n"
    "600000.SH,2024-01-03,10.2,10.6,10.1,10.4,1100,11440\n"
    "000001.SZ,2024-01-02,8,8.2,7.9,8.1,500,4050\n"
    "000001.SZ,2024-01-03,8.1,7.5,7.9,8.0,500,4000\n"
)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr("wequant.data.sources.baostock.import_baostock", market)


def context(home, **hooks):
    return Context(home, clock=lambda: NOW.isoformat(), **hooks)


def own_folder(tmp_path, bars=BARS):
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "bars.csv").write_text(bars, encoding="utf-8")
    return folder


def test_a_build_and_its_history_are_versions_of_their_own(tmp_path, fake):
    home = tmp_path / "home"
    built = service.data_build(service.BuildRequest(years=1), context(home))
    history = service.data_backfill(service.BackfillRequest(oldest=2024), context(home))
    assert (built.status, built.version, history.status, history.segments) == ("built", 1, "complete", [])


def test_a_cancelled_build_is_interrupted_and_leaves_no_version(tmp_path, fake):
    with pytest.raises(ServiceError) as raised:
        service.data_build(service.BuildRequest(years=1), context(tmp_path / "home", cancel=lambda: True))
    assert raised.value.exit == Exit.INTERRUPTED and Store(tmp_path / "home").manifest() is None


def test_a_check_reports_each_rule_its_rows_break_and_writes_nothing(tmp_path):
    folder = own_folder(tmp_path)
    found = service.data_own_check(service.OwnRequest(path=str(folder)), context(tmp_path / "home"))
    assert found.ok and {p.rule: p.rows for p in found.problems} == {
        "duplicate": 1,
        "price_order": 1,
        "no_list_date": 2,
    }
    assert sorted(p.name for p in folder.iterdir()) == ["bars.csv"] and not (tmp_path / "home").exists()


def test_files_without_a_required_column_cannot_be_imported(tmp_path):
    folder = own_folder(tmp_path, bars="code,date,close\n600000.SH,2024-01-02,10\n")
    found = service.data_own_check(service.OwnRequest(path=str(folder)), context(tmp_path / "home"))
    with pytest.raises(ServiceError) as raised:
        service.data_own_import(service.OwnRequest(path=str(folder)), context(tmp_path / "home"))
    assert not found.ok and raised.value.detail[0].code == "bad_file"


def test_an_import_is_one_version_that_records_what_it_did_not_take_as_given(tmp_path):
    home = tmp_path / "home"
    imported = service.data_own_import(service.OwnRequest(path=str(own_folder(tmp_path))), context(home))
    issues = Store(home).read_table("data_issues")
    assert (imported.version, imported.days, imported.codes) == (1, 2, 2)
    assert sorted(issues["kind"]) == ["own_price_order", "own_row_dropped"]


def test_a_cancelled_import_leaves_no_version(tmp_path):
    with pytest.raises(ServiceError) as raised:
        service.data_own_import(
            service.OwnRequest(path=str(own_folder(tmp_path))), context(tmp_path / "home", cancel=lambda: True)
        )
    assert raised.value.exit == Exit.INTERRUPTED and Store(tmp_path / "home").manifest() is None


def test_an_import_needs_an_empty_data_root(tmp_path):
    home = tmp_path / "home"
    service.data_own_import(service.OwnRequest(path=str(own_folder(tmp_path))), context(home))
    with pytest.raises(ServiceError) as raised:
        service.data_own_import(service.OwnRequest(path=str(tmp_path / "mine")), context(home))
    assert raised.value.detail[0].code == "store_exists"
