"""The daily update (S2.5) against a fake BaoStock: each test names the rule it guards. Synthetic data only."""

import datetime as dt

import polars as pl
import pytest

from tests.baostock_raw_fake import D3
from tests.update_fake import AFTER_CLOSE, D4, D5, NAMES, Market, rows, seed_store
from wequant.data.merge.resolutions import default_path, load
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import BaoStock
from wequant.data.store import version_reason
from wequant.data.update import UpdateCancelled, due_days, update


@pytest.fixture
def store(tmp_path):
    return seed_store(tmp_path / "home")


def run(store, market, now=AFTER_CLOSE[D4], **callbacks):
    return update(store, BaoStock(client=market), now, **callbacks)


def test_an_update_merges_the_closed_days_after_the_last_one_as_one_version_made_for_it(store):
    outcome = run(store, Market(days={D4: rows(D4)}))
    manifest = store.manifest()
    assert (outcome.status, outcome.days, manifest["version"], manifest["last_trade_date"]) == (
        "updated",
        [D4],
        2,
        "2024-01-05",
    )
    assert version_reason(manifest) == {"code": "daily_update", "params": {"days": 1}}
    assert store.read_table("bars", start=D4)["code"].to_list() == ["600000.SH", "600001.SH"]


def test_a_second_update_makes_no_version(store):
    market = Market(days={D4: rows(D4)})
    run(store, market)
    assert run(store, market).status == "up_to_date" and store.versions() == [1, 2]


def test_every_missed_day_comes_in_with_one_update(store):
    outcome = run(store, Market(days={D4: rows(D4), D5: rows(D5)}), now=AFTER_CLOSE[D5])
    assert outcome.days == [D4, D5] and store.versions() == [1, 2]


def test_a_day_not_published_yet_ends_the_fetch_and_the_days_before_it_come_in(store):
    outcome = run(store, Market(days={D4: rows(D4)}), now=AFTER_CLOSE[D5])
    assert (outcome.status, outcome.days, outcome.waiting) == ("updated", [D4], D5)


def test_without_a_published_day_nothing_is_written(store):
    outcome = run(store, Market())
    assert (outcome.status, outcome.waiting, store.versions()) == ("not_published", D4, [1])


def test_today_is_due_only_once_its_bars_are_published():
    dates = pl.DataFrame({"calendar_date": [D3.isoformat(), D4.isoformat()], "is_trading_day": ["1", "1"]})
    before = dt.datetime(2024, 1, 5, 9, 59, tzinfo=dt.UTC)  # 17:59 in Shanghai
    assert (due_days(dates, D3, before), due_days(dates, D3, AFTER_CLOSE[D4])) == ([], [D4])


def test_the_calendar_refresh_keeps_earlier_rows_and_replaces_the_rest(store):
    """The update asks from the start of the store's last year (2024): the file's 2023 rows stay, 2024's are new."""
    raw = BaoStockRaw.at(store.home)
    old = [["2023-12-29", "1", "old"], ["2024-01-02", "0", "old"]]
    raw.write(raw.trade_dates, pl.DataFrame(old, schema=["calendar_date", "is_trading_day", "_request"], orient="row"))
    run(store, Market(days={D4: rows(D4)}))
    found = pl.read_parquet(raw.trade_dates).select("calendar_date", "is_trading_day", old=pl.col("_request") == "old")
    assert found.filter(pl.col("calendar_date") <= "2024-01-02").rows() == [
        ("2023-12-29", "1", True),
        ("2024-01-02", "1", False),
    ]


def test_names_and_official_listing_dates_come_with_the_update(store):
    run(store, Market(days={D4: rows(D4)}))
    securities = store.read_table("securities")
    assert dict(securities.select("code", "name").rows()) == NAMES
    assert set(securities["list_date"]) == {dt.date(2023, 1, 3)}


def test_an_event_on_a_new_day_extends_the_code_s_factors(store):
    """600001.SH pays out on D4: its exchange preclose is D3's close / 1.04, and BaoStock's factor steps by 1.04."""
    day = rows(D4, **{"600001.SH": {"preclose": "51.9200"}})  # 54.00 / 1.04 = 51.92
    history = {"600001.SH": [(dt.date(2023, 1, 3), 1.0), (D4, 1.04)]}
    run(store, Market(days={D4: day}, events={D4: [("600001.SH", 1.04)]}, histories=history))
    factors = store.read_table("adj_factor").filter(pl.col("code") == "600001.SH")
    assert factors.select("ex_date", "factor").rows()[-1] == (D4, 1.04)


def test_the_new_days_bring_each_stored_index_s_bars(store):
    run(store, Market(days={D4: rows(D4)}))
    index = store.read_table("index_bars", start=D4)
    assert index.select("code", "date", "close").rows() == [("000300.SH", D4, 3_100_000)]


def test_a_conflict_becomes_a_proposed_resolution_and_the_store_is_unchanged(store):
    """1,100 shares for 1,000 yuan at 105.00 a share: the amount the merge cannot keep waits for a decision."""
    day = rows(D4, **{"600000.SH": {"volume": "1100", "amount": "1000.0000"}})
    outcome = run(store, Market(days={D4: day}))
    entries = load(default_path(store.home))
    assert (outcome.status, outcome.proposed, store.versions()) == ("conflicts", 1, [1])
    assert entries.select("code", "date", "status").rows() == [("600000.SH", D4, "proposed")]


def test_a_cancelled_update_commits_nothing_and_frees_the_lock(store):
    calls = iter([False, True])  # the first day's check passes, the commit's does not
    with pytest.raises(UpdateCancelled):
        run(store, Market(days={D4: rows(D4)}), cancel=lambda: next(calls))
    assert store.versions() == [1] and run(store, Market(days={D4: rows(D4)})).status == "updated"
