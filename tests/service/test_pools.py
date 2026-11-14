"""Stock pools in the service (round 3): a strategy keeps the list it copied, the scope warnings, an index's members as
a list, and the older index options still running. On tests.service.conftest's synthetic market."""

import pytest

from tests.frames import table_frame
from tests.service.conftest import DAYS, SCREENED
from wequant import service
from wequant.data.store import Store
from wequant.service import Context
from wequant.service.details import validated
from wequant.service.pools import PoolDrift

LISTED = ["000001.SZ", "600004.SH"]


def members(home, index_code: str, spans: list[tuple[str, int, int]]) -> None:
    """A new version whose index_members are spans (code, first day, last day) of index_code, days by DAYS index."""
    rows = [{"index_code": index_code, "code": c, "start_date": DAYS[a], "end_date": DAYS[b]} for c, a, b in spans]
    Store(home).commit({}, replace={"index_members": table_frame("index_members", rows)})


def strategy(**pool) -> service.StrategyRequest:
    return validated(service.StrategyRequest, {"strategy": {**SCREENED, "pool": pool}})


def copied(pool: service.PoolOut) -> dict:
    return {"codes": [s.code for s in pool.stocks], "pool_id": pool.id, "pool_name": pool.name}


def test_a_strategy_keeps_the_list_it_copied_when_its_pool_changes(market_home):
    context = Context(market_home)
    pool = service.pools_save(service.PoolSaveRequest(name="自选", codes=LISTED), context)
    saved = {**SCREENED, "pool": {"stocks": copied(pool)}}
    service.strategy_save(validated(service.SaveRequest, {"strategy": saved, "id": "c"}), context)
    refill = service.PoolSaveRequest(id=pool.id, name="自选", codes=["000001.SZ", "600006.SH"])
    service.pools_save(refill, context)
    shown = service.strategy_show(service.ShowRequest(id="c"), context)
    assert shown.strategy.pool.stocks.codes == LISTED
    assert shown.pool_drift == PoolDrift(pool_id=pool.id, pool_name="自选", added=["600006.SH"], removed=["600004.SH"])


def test_a_strategy_whose_pool_was_deleted_says_so(market_home):
    context = Context(market_home)
    pool = service.pools_save(service.PoolSaveRequest(name="自选", codes=LISTED), context)
    service.pools_delete(service.PoolRequest(id=pool.id), context)
    drift = service.strategy_validate(strategy(stocks=copied(pool)), context).pool_drift
    assert drift == PoolDrift(pool_id=pool.id, pool_name=None, added=[], removed=LISTED)


def test_a_strategy_on_a_fixed_list_picks_only_from_it(market_home):
    request = service.ScreenRequest(strategy=strategy(stocks={"codes": LISTED}).strategy, date=DAYS[-1])
    picked = {row.code for row in service.screen(request, Context(market_home)).rows}
    assert picked and picked <= set(LISTED)


def test_codes_the_data_lacks_are_a_warning_not_an_error(market_home):
    checked = service.strategy_validate(strategy(stocks={"codes": [*LISTED, "600009.SH"]}), Context(market_home))
    assert [(w.code, w.params) for w in checked.warnings] == [
        ("stocks_not_in_data", {"count": 1, "codes": "600009.SH"})
    ]


def test_a_strategy_on_an_older_index_still_validates_and_runs(market_home):
    """000906.SH (CSI 800) is no longer offered; where the store has its members it reads them as before."""
    members(market_home, "000906.SH", [("000001.SZ", 0, 7), ("600004.SH", 0, 7)])
    context, request = Context(market_home), strategy(index="000906.SH")
    checked = service.strategy_validate(request, context)
    screened = service.screen(service.ScreenRequest(strategy=request.strategy, date=DAYS[-1]), context)
    run = service.backtest(service.BacktestRequest(strategy=request.strategy, save=False), context)
    assert [w.code for w in checked.warnings] == ["legacy_index"]
    assert {row.code for row in screened.rows} <= set(LISTED) and run.metrics


def test_an_index_s_list_is_its_members_on_the_last_day_with_members(market_home):
    """Spans end on DAYS[5], two days before the store's last day: the members of DAYS[5]."""
    members(market_home, "399300.SZ", [("000001.SZ", 0, 5), ("600000.SH", 0, 3), ("600004.SH", 4, 5)])
    found = service.pools_from_index(service.IndexPoolRequest(index="000300.SH"), Context(market_home))
    assert (found.date.isoformat(), [s.code for s in found.stocks]) == (DAYS[5], LISTED)


def test_an_exported_pool_imports_as_the_same_list(market_home):
    context = Context(market_home)
    pool = service.pools_save(service.PoolSaveRequest(name="自选", codes=["600004.SH", "000001.SZ"]), context)
    csv = service.pools_export(service.PoolRequest(id=pool.id), context).csv
    imported = service.pools_import(service.PoolImportRequest(name="副本", text=csv), context)
    assert [s.code for s in imported.pool.stocks] == ["600004.SH", "000001.SZ"]
    assert {u.reason for u in imported.unparsed} == {"header"}


@pytest.mark.parametrize("call", [service.pools_show, service.pools_delete, service.pools_export])
def test_an_unknown_pool_is_not_found(market_home, call):
    with pytest.raises(service.ServiceError) as raised:
        call(service.PoolRequest(id="p-00000000"), Context(market_home))
    assert (raised.value.exit, raised.value.detail[0].code) == (service.Exit.NOT_FOUND, "unknown_pool")
