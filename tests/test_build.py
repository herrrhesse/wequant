"""The first build and the history behind it (round 3, S2.9), against a fake BaoStock: one version from an empty data
root, the same version after a stop and a restart, nothing left by a cancel, and earlier years one segment each."""

import datetime as dt

import pytest

from tests.baostock_raw_fake import C1, C2, CALENDAR, D1, D3, day_row
from tests.update_fake import NAMES, Market
from wequant.data.build import backfill, build
from wequant.data.sources.baostock import BaoStock
from wequant.data.store import Store, version_reason
from wequant.data.update import UpdateCancelled

NOW = dt.datetime(2024, 1, 4, 10, 0, tzinfo=dt.UTC)  # 18:00 in Shanghai: D3 has closed
CLOSES = {"600000.SH": [10000, 10100, 10200, 10300, 10400], "600001.SH": [5000, 5100, 5200, 5300, 5400]}


def market() -> Market:
    """Both stocks on every day of the calendar, 600000.SH a CSI 300 member, flat factors."""
    days = {
        day: [day_row(c, day, closes[n], preclose=closes[n - 1] if n else None) for c, closes in CLOSES.items()]
        for n, day in enumerate(CALENDAR)
    }
    histories = {code: [(dt.date(2023, 1, 3), 1.0)] for code in CLOSES}
    return Market(days=days, trading=CALENDAR, histories=histories, members={"query_hs300_stocks": ["600000.SH"]})


def run(home, cancel=None, start=D1):
    return build(Store(home), BaoStock(client=market()), years=1, now=NOW, cancel=cancel, start=start)


def tables(home) -> dict:
    """Each table's files by their hashes: what two versions built alike must share."""
    return {name: [f["sha256"] for f in t["files"]] for name, t in Store(home).manifest()["tables"].items()}


def test_a_first_build_makes_one_version_from_baostock_alone(tmp_path):
    built = run(tmp_path / "home")
    store = Store(tmp_path / "home")
    securities = store.read_table("securities")
    assert (built.status, built.version, store.versions(), version_reason(store.manifest())["code"]) == (
        "built",
        1,
        [1],
        "first_build",
    )
    assert store.read_table("bars").height == 6 and dict(securities.select("code", "name").rows()) == NAMES
    assert store.read_table("index_members").select("code", "start_date", "end_date").rows() == [("600000.SH", D1, D3)]


def test_a_build_stopped_half_way_leaves_no_version_and_carries_on_to_the_same_one(tmp_path):
    asked = iter([False, False, True])  # stops at the third day
    with pytest.raises(UpdateCancelled):
        run(tmp_path / "stopped", cancel=lambda: next(asked, False))
    assert Store(tmp_path / "stopped").manifest() is None
    run(tmp_path / "stopped")
    run(tmp_path / "straight")
    assert tables(tmp_path / "stopped") == tables(tmp_path / "straight")


def test_a_second_build_makes_no_version(tmp_path):
    run(tmp_path / "home")
    assert (run(tmp_path / "home").status, Store(tmp_path / "home").versions()) == ("exists", [1])


def test_earlier_years_come_in_one_version_per_segment_until_none_are_left(tmp_path):
    run(tmp_path / "home")
    store = Store(tmp_path / "home")
    added = backfill(store, BaoStock(client=market()), NOW, oldest=2023)
    assert (added.status, added.first, added.last, version_reason(store.manifest())["code"]) == (
        "built",
        C1,
        C2,
        "history_backfill",
    )
    assert store.read_table("calendar")["date"].to_list() == list(CALENDAR)
    assert backfill(store, BaoStock(client=market()), NOW, oldest=2023).status == "complete"


def test_a_cancelled_segment_adds_no_version(tmp_path):
    run(tmp_path / "home")
    store = Store(tmp_path / "home")
    with pytest.raises(UpdateCancelled):
        backfill(store, BaoStock(client=market()), NOW, cancel=lambda: True, oldest=2023)
    assert store.versions() == [1]
