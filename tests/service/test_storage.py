"""Storage operations (round 2): the version in use, pins, cleanup and usage, run deletion, the archive's limit on
save, exports and imports, and when an update is due. Synthetic homes only."""

import datetime as dt

import polars as pl
import pytest

from wequant import service
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.data.store import Store
from wequant.runs.archive import RUNS, Archive
from wequant.service import Context, ServiceError
from wequant.service.jobs import KINDS
from wequant.service.queue import Jobs
from wequant.storage.settings import change


def backtest(home, cash: float = 1_000_000.0) -> str:
    request = service.BacktestRequest(saved={"id": "m"}, cash=cash, benchmark=None)
    return service.backtest(request, Context(home)).run_id


def more_versions(home, n: int) -> None:
    store = Store(home)
    for i in range(n):
        store.commit({"note": str(i)})


def test_a_request_without_a_version_reads_the_version_in_use_until_it_goes_back(home_v2):
    service.data_use_version(service.UseVersionRequest(version=1), Context(home_v2))
    status = service.data_status(service.StatusRequest(), Context(home_v2))  # a new context: the setting is on disk
    bars = service.data_bars(service.BarsRequest(codes=["600000.SH"]), Context(home_v2))
    assert (status.data_version, status.version_in_use, status.using_old_version, bars.data_version) == (1, 1, True, 1)
    service.data_use_version(service.UseVersionRequest(version=None), Context(home_v2))
    assert service.data_status(service.StatusRequest(), Context(home_v2)).data_version == 2


def test_only_a_kept_version_can_be_in_use(home):
    with pytest.raises(ServiceError) as caught:
        service.data_use_version(service.UseVersionRequest(version=9), Context(home))
    assert [d.code for d in caught.value.detail] == ["version_not_kept"]


def test_a_job_pins_the_version_in_use(home_v2, tmp_path):
    service.data_use_version(service.UseVersionRequest(version=1), Context(home_v2))
    jobs = Jobs(home_v2, KINDS, tmp_path / "cache", limit=0)  # nothing starts
    request = service.JobStartRequest(kind="backtest", request={"strategy": {"top_n": 1}})
    assert service.jobs_start(request, Context(home_v2, jobs=jobs)).data_version == 1


def test_cleanup_removes_what_usage_says_it_would_free(market_home):
    more_versions(market_home, 6)
    change(market_home, lambda settings: {"keep_versions": 2})  # no tidy yet
    cleaned = service.storage_cleanup(service.CleanupRequest(), Context(market_home))
    before, after = cleaned.before, cleaned.after
    assert before.free_versions > 0 and after.free == 0 and Store(market_home).versions() == [6, 7]


def test_saving_a_run_over_the_archive_s_limit_removes_the_oldest_unpinned_run(market_home):
    first = backtest(market_home)
    change(market_home, lambda settings: {"runs_limit_gb": 1e-12})
    second = backtest(market_home, cash=2_000_000.0)
    assert list(Archive(market_home / RUNS).index()["id"]) == [second] != [first]


def test_deleting_a_run_removes_it_and_its_pin(market_home):
    run = backtest(market_home)
    service.runs_pin(service.PinRunRequest(id=run), Context(market_home))
    service.runs_delete(service.DeleteRunRequest(id=run), Context(market_home))
    settings = service.settings_show(service.SettingsRequest(), Context(market_home))
    assert (run in Archive(market_home / RUNS), settings.pinned_runs) == (False, [])


def test_a_run_still_names_its_version_once_that_version_is_cleaned(market_home):
    run = backtest(market_home)
    name = service.data_status(service.StatusRequest(), Context(market_home)).data_version_name
    more_versions(market_home, 2)
    service.settings_update(service.SettingsUpdate(keep_versions=1), Context(market_home))  # tidies: version 1 goes
    row = service.runs_list(service.RunsListRequest(), Context(market_home)).rows[0]
    assert (row.id, row.data_version_name, row.rerunnable, Store(market_home).versions()) == (run, name, False, [3])


def test_a_file_that_is_not_an_export_is_refused(home, tmp_path):
    (tmp_path / "x.wqver").write_text("not a zip")
    with pytest.raises(ServiceError) as caught:
        service.data_import(service.ImportRequest(path=str(tmp_path / "x.wqver")), Context(home))
    assert [d.code for d in caught.value.detail] == ["bad_file"]


def test_a_data_root_of_links_to_another_store_refuses_cleanup_and_still_reads(home, tmp_path):
    """The links' targets stay as they were; reading through the links works."""
    more_versions(home, 6)
    view = tmp_path / "view"
    view.mkdir()
    for path in home.iterdir():
        (view / path.name).symlink_to(path)
    before = sorted(p for p in home.rglob("*") if p.is_file())
    with pytest.raises(ServiceError) as caught:
        service.storage_cleanup(service.CleanupRequest(), Context(view))
    status = service.data_status(service.StatusRequest(), Context(view))
    assert [d.code for d in caught.value.detail] == ["outside_data_root"] and status.current_version == 7
    assert sorted(p for p in home.rglob("*") if p.is_file()) == before


def test_an_update_is_due_once_the_next_trading_day_has_closed(home):
    last = Store(home).read_table("calendar")["date"].max()
    following = last + dt.timedelta(days=3 if last.weekday() == 4 else 1)
    dates = pl.DataFrame({"calendar_date": [following.isoformat()], "is_trading_day": ["1"]})
    raw = BaoStockRaw.at(home)
    raw.write(raw.trade_dates, raw_frame(dates, "query_trade_dates()", "test"))
    at = lambda hour: Context(home, clock=lambda: f"{following.isoformat()}T{hour:02d}:00:00+00:00")  # noqa: E731
    before, after = (service.data_status(service.StatusRequest(), at(hour)) for hour in (9, 10))  # 17:00, 18:00
    assert (before.next_trading_day, before.update_due, after.update_due) == (following, False, True)
