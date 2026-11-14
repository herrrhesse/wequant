"""The first build and the history behind it (round 3, S2.9), against a fake BaoStock: one version from an empty data
root, the same version after a stop and a restart, nothing left by a cancel, and earlier years one segment each."""

import datetime as dt

import polars as pl
import pytest

from tests.baostock_raw_fake import C1, C2, CALENDAR, D1, D2, D3, day_row
from tests.update_fake import AFTER_CLOSE, D4, D5, NAMES, Market
from wequant.data.build import backfill, build, extend, join
from wequant.data.indexes import MEMBERS_SINCE
from wequant.data.scope import ALL, Layer, Scope, layers
from wequant.data.sources.baostock import BaoStock
from wequant.data.store import Store, version_reason
from wequant.data.update import UpdateCancelled, update

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


def scoped_market() -> Market:
    """Four stocks on every day: CSI 300 lists 600000.SH and 600001.SH until D3, then 600000.SH and 600002.SH (one
    leaves, one joins); 600003.SH is never a member."""
    opens = {"600000.SH": 10000, "600001.SH": 5000, "600002.SH": 2000, "600003.SH": 3000}
    days = {
        day: [
            day_row(c, day, close + 100 * n, preclose=close + 100 * (n - 1) if n else None)
            for c, close in opens.items()
        ]
        for n, day in enumerate(CALENDAR)
    }
    members = lambda date: ["600000.SH", "600002.SH"] if date >= D3.isoformat() else ["600000.SH", "600001.SH"]  # noqa: E731
    return Market(
        days=days,
        trading=CALENDAR,
        histories={code: [(dt.date(2023, 1, 3), 1.0)] for code in opens},
        members={"query_hs300_stocks": members},
        names={code: f"股票{code[:6]}" for code in opens},
    )


CSI300 = Scope(indexes=("000300.SH",))


def run(home, cancel=None, start=D1):
    return build(Store(home), BaoStock(client=market()), years=1, now=NOW, cancel=cancel, start=start, scope=ALL)


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


def test_a_build_reports_the_time_it_has_left_once_it_has_fetched(tmp_path):
    """F-33: eta_seconds from the build's own pace; unknown before the first fetch."""
    steps = []
    build(
        Store(tmp_path / "home"),
        BaoStock(client=market()),
        years=1,
        now=NOW,
        progress=steps.append,
        start=D1,
        scope=ALL,
    )
    left = [s.params["eta_seconds"] for s in steps if s.stage == "build_stocks"]
    assert left[0] is None and all(isinstance(seconds, int | float) for seconds in left[1:])


def test_a_backfill_s_steps_name_its_years(tmp_path):
    run(tmp_path / "home")
    steps = []
    backfill(Store(tmp_path / "home"), BaoStock(client=market()), NOW, progress=steps.append, oldest=2023)
    assert {(s.params["first_year"], s.params["last_year"]) for s in steps} == {(2023, 2024)}


def test_an_index_build_holds_every_stock_that_was_a_member_at_some_time_in_its_window(tmp_path):
    """No survivorship: the member that left and the one that joined are in, the stock never listed is not (S2.11)."""
    store = Store(tmp_path / "home")
    build(store, BaoStock(client=scoped_market()), years=1, now=NOW, start=D1, scope=CSI300)
    assert sorted(set(store.read_table("bars")["code"])) == ["600000.SH", "600001.SH", "600002.SH"]


def test_a_scoped_build_stopped_half_way_carries_on_to_the_same_version(tmp_path):
    asked = iter([False, False, True])

    def scoped(home, cancel=None):
        return build(Store(home), BaoStock(client=scoped_market()), 1, NOW, cancel=cancel, start=D1, scope=CSI300)

    with pytest.raises(UpdateCancelled):
        scoped(tmp_path / "stopped", cancel=lambda: next(asked, False))
    scoped(tmp_path / "stopped")
    scoped(tmp_path / "straight")
    assert tables(tmp_path / "stopped") == tables(tmp_path / "straight")


def scoped(home, scope=CSI300):
    store = Store(home)
    build(store, BaoStock(client=scoped_market()), 1, NOW, start=D1, scope=scope)
    return store


def test_a_wider_scope_brings_in_its_stocks_and_one_layer_from_the_first_day(tmp_path):
    store = scoped(tmp_path / "home")
    added = extend(store, BaoStock(client=scoped_market()), ALL, NOW, name="沪深全部")
    found = store.manifest()
    assert (added.status, version_reason(found)["code"], sorted(set(store.read_table("bars")["code"]))) == (
        "built",
        "scope_extend",
        ["600000.SH", "600001.SH", "600002.SH", "600003.SH"],
    )
    assert layers(found, D1) == [Layer(ALL, D1)]
    assert extend(store, BaoStock(client=scoped_market()), ALL, NOW).status == "covered"


def test_a_wider_scope_keeps_the_stored_rows_as_they_were(tmp_path):
    store = scoped(tmp_path / "home")
    before = store.read_table("bars")
    extend(store, BaoStock(client=scoped_market()), ALL, NOW)
    assert store.read_table("bars").filter(pl.col("code") != "600003.SH").equals(before)


def test_a_cancelled_extension_adds_no_version(tmp_path):
    store = scoped(tmp_path / "home")
    with pytest.raises(UpdateCancelled):
        extend(store, BaoStock(client=scoped_market()), ALL, NOW, cancel=lambda: True)
    assert store.versions() == [1] and layers(store.manifest(), D1) == [Layer(CSI300, D1)]


def joining_market() -> Market:
    """Four stocks through D5: CSI 300 lists 600000.SH and 600001.SH, then from D5 600000.SH and 600003.SH."""
    opens = {"600000.SH": 10000, "600001.SH": 5000, "600002.SH": 2000, "600003.SH": 3000}
    days = {
        day: [
            day_row(c, day, close + 100 * n, preclose=close + 100 * (n - 1) if n else None)
            for c, close in opens.items()
        ]
        for n, day in enumerate((*CALENDAR, D4, D5))
    }
    members = lambda date: ["600000.SH", "600003.SH"] if date >= D5.isoformat() else ["600000.SH", "600001.SH"]  # noqa: E731
    return Market(
        days=days,
        histories={code: [(dt.date(2023, 1, 3), 1.0)] for code in opens},
        members={"query_hs300_stocks": members},
        names={code: f"股票{code[:6]}" for code in opens},
    )


def test_an_update_brings_in_a_new_member_with_its_history_from_the_layer_s_first_day(tmp_path):
    store = Store(tmp_path / "home")
    build(store, BaoStock(client=joining_market()), 1, NOW, start=D1, scope=CSI300)
    source = BaoStock(client=joining_market())
    update(store, source, AFTER_CLOSE[D5])
    joined = join(store, source, D3, AFTER_CLOSE[D5])
    bars = store.read_table("bars")
    assert joined == 1 and bars.filter(pl.col("code") == "600003.SH")["date"].to_list() == [D1, D2, D3, D4, D5]
    assert sorted(set(bars["code"])) == ["600000.SH", "600001.SH", "600003.SH"]


def test_a_backfill_by_index_stops_at_the_index_s_first_member_list(tmp_path, monkeypatch):
    """Earlier history than the first member list has no scope: the backfill stops there and says why (S2.11)."""
    monkeypatch.setitem(MEMBERS_SINCE, "000300.SH", C2)
    store = scoped(tmp_path / "home")
    added = backfill(store, BaoStock(client=scoped_market()), NOW, oldest=2023)
    stopped = backfill(store, BaoStock(client=scoped_market()), NOW, oldest=2023)
    assert (added.first, stopped.status, stopped.floor) == (C2, "complete", ("000300.SH", C2))


def test_a_short_list_s_update_fetches_each_stock_s_days_instead_of_the_day_s_cross_section(tmp_path):
    """One stock costs less than a day of every stock (S2.11 probe: 0.8 s against 60 s)."""
    store = Store(tmp_path / "home")
    build(store, BaoStock(client=joining_market()), 1, NOW, start=D1, scope=Scope(stocks=("600002.SH",)))
    market = joining_market()
    update(store, BaoStock(client=market), AFTER_CLOSE[D5])
    asked = {method for method, _ in market.calls}
    days = store.read_table("bars").filter(pl.col("code") == "600002.SH")["date"].to_list()
    assert "query_daily_history_k_AStock" not in asked and days == [D1, D2, D3, D4, D5]
