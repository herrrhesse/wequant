"""The run archive through the service: two runs of the synthetic market (tests.service.conftest.MARKET).

a is saved strategy m (weekly: decisions on 2024-01-05 and 2024-01-11, buys filled on 2024-01-08); b trades daily
with half the cash and is not saved. 600004.SH, held by a, has anomalous closes on 2024-01-09 and 2024-01-10.
"""

import datetime as dt
import json

import pytest

from tests.frames import table_frame
from tests.service.conftest import SCREENED
from wequant.data.store import Store
from wequant.service import Context, Exit, ServiceError
from wequant.service.backtest import BacktestRequest, backtest
from wequant.service.explain import ExplainRequest, explain
from wequant.service.runs import (
    AnomaliesRequest,
    OrdersRequest,
    RunDayRequest,
    RunDiffRequest,
    RunRequest,
    RunsListRequest,
    runs_anomalies,
    runs_day,
    runs_diff,
    runs_list,
    runs_orders,
    runs_rerun,
    runs_show,
)
from wequant.service.specs import StrategySpec

DAILY = {**SCREENED, "period": "day"}
ISSUE = {
    "table": "bars",
    "code": "600004.SH",
    "date": "2024-01-09",
    "kind": "price_mismatch",
    "action": "resolved_baostock",
}


@pytest.fixture
def archived(market_home):
    issues = [ISSUE, {**ISSUE, "date": "2024-01-10"}]
    Store(market_home).commit({}, replace={"data_issues": table_frame("data_issues", issues, source="test")})
    context = Context(market_home)
    a = backtest(BacktestRequest(saved={"id": "m"}), context)
    b = backtest(BacktestRequest(strategy=DAILY, cash=500_000.0), context)
    return context, a, b


def test_the_list_is_newest_first_filters_by_strategy_and_pages(archived):
    context, a, b = archived
    made = context.store().home / "runs" / a.run_id / "provenance.json"
    made.write_text(json.dumps({**json.loads(made.read_text()), "created_at": "2000-01-01T00:00:00+00:00"}))
    page = runs_list(RunsListRequest(), context)
    assert [(r.id, [s.id for s in r.saved_as], r.rerunnable) for r in page.rows] == [
        (b.run_id, [], True),
        (a.run_id, ["m"], True),
    ]
    assert [r.id for r in runs_list(RunsListRequest(strategy_hash=a.strategy_hash), context).rows] == [a.run_id]
    pages = [runs_list(RunsListRequest(limit=1, offset=offset), context) for offset in (0, 1)]
    assert [(page.total, [r.id for r in page.rows]) for page in pages] == [(2, [b.run_id]), (2, [a.run_id])]


def test_show_gives_what_was_run_and_daily_rows_that_agree_with_the_metrics(archived):
    context, _, b = archived
    shown = runs_show(RunRequest(id=b.run_id), context)
    assert (shown.cash, shown.strategy, shown.benchmark) == (500_000.0, StrategySpec.model_validate(DAILY), "000300.SH")
    total = shown.metrics["total_return"].value
    assert (shown.daily[0].date, shown.daily[0].nav, shown.daily[-1].date) == (b.start, 1.0, b.end)
    assert shown.daily[-1].nav - 1 == pytest.approx(total) and shown.periods.year[0].return_ == pytest.approx(total)
    assert "stamp-2023" in [fee.id for fee in shown.fees]


def test_a_rerun_matches_the_archive_until_its_store_version_is_gone(archived):
    context, a, _ = archived
    assert runs_rerun(RunRequest(id=a.run_id), context).identical
    store = context.store()
    store.commit({"note": "later"})
    store.commit({"note": "later still"})
    store.cleanup(keep=1, dry_run=False)
    with pytest.raises(ServiceError) as raised:
        runs_rerun(RunRequest(id=a.run_id), context)
    assert raised.value.exit == Exit.NOT_FOUND and [d.code for d in raised.value.detail] == ["version_not_kept"]
    assert not runs_list(RunsListRequest(), context).rows[0].rerunnable


def test_a_diff_lists_the_differing_inputs_and_only_the_differing_metrics(archived):
    context, a, b = archived
    same = runs_diff(RunDiffRequest(a=a.run_id, b=a.run_id), context)
    assert (same.inputs, same.metrics) == ([], [])
    diff = runs_diff(RunDiffRequest(a=a.run_id, b=b.run_id), context)
    assert any(line.startswith("description.cash: ") for line in diff.inputs)
    assert diff.metrics and all(m.a != m.b for m in diff.metrics)


def test_orders_are_filtered_and_paged_in_archive_order(archived):
    context, a, _ = archived
    every = runs_orders(OrdersRequest(id=a.run_id), context)
    buys = runs_orders(OrdersRequest(id=a.run_id, side="buy", code="600004.SH"), context)
    assert [(r.date, r.code, r.side) for r in buys.rows] == [(dt.date(2024, 1, 8), "600004.SH", "buy")]
    page = runs_orders(OrdersRequest(id=a.run_id, limit=1, offset=1), context)
    assert (page.total, page.rows) == (every.total, every.rows[1:2])


def test_a_day_shows_the_holdings_by_value_and_the_decisions_taken_at_its_close(archived):
    context, a, _ = archived
    filled = runs_day(RunDayRequest(id=a.run_id, date="2024-01-08"), context)
    assert [p.code for p in filled.positions] == ["600004.SH", "000001.SZ"]  # 9.20 x 50,000 > 10.80 x 40,000
    assert filled.positions[0].weight == pytest.approx(460_000 / filled.equity) and not filled.decision_day
    assert {o.code for o in filled.orders} == {"600004.SH", "000001.SZ"} and filled.decisions == []
    decided = runs_day(RunDayRequest(id=a.run_id, date="2024-01-05"), context)
    assert [(d.code, d.role, d.rank, d.action) for d in decided.decisions] == [
        ("600004.SH", "target", 1, "buy"),
        ("000001.SZ", "target", 2, "buy"),
        ("600006.SH", "backup", 3, "none"),
    ]


def test_anomaly_uses_are_filtered_by_channel_and_paged(archived):
    context, a, _ = archived
    every = runs_anomalies(AnomaliesRequest(id=a.run_id), context)  # the last decision has no fill day: no signal use
    assert [(u.code, u.channel, u.anomaly_date) for u in every.rows] == [
        ("600004.SH", "valuation", dt.date(2024, 1, 9)),
        ("600004.SH", "valuation", dt.date(2024, 1, 10)),
    ]
    pages = (({"channel": "signal"}, 0, []), ({"code": "000001.SZ"}, 0, []), ({"limit": 1}, 2, every.rows[:1]))
    for request, total, rows in (*pages, ({"offset": 1}, 2, every.rows[1:])):
        page = runs_anomalies(AnomaliesRequest(id=a.run_id, **request), context)
        assert (page.total, page.rows) == (total, rows)


FAILURES = [
    (lambda c, a: runs_show(RunRequest(id="0" * 16), c), "unknown_run", ["id"]),
    (lambda c, a: runs_diff(RunDiffRequest(a=a, b="nope"), c), "unknown_run", ["b"]),
    (lambda c, a: runs_day(RunDayRequest(id=a, date="2024-01-06"), c), "not_a_trading_day", ["date"]),
]


@pytest.mark.parametrize("call, code, loc", FAILURES, ids=[f"{case[1]} {case[2]}" for case in FAILURES])
def test_what_the_archive_does_not_have(archived, call, code, loc):
    context, a, _ = archived
    with pytest.raises(ServiceError) as raised:
        call(context, a.run_id)
    assert raised.value.exit == Exit.NOT_FOUND and [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


EXITING = {  # 000001.SZ closes 10.60 on 2024-01-05, leaves the pool and is sold whole at the next open
    "filters": [{"name": "cheap", "expr": ["LT", "close", 10.5]}],
    "factors": [{"name": "level", "expr": "close", "prefer": "low"}],
    "top_n": 2,
    "period": "day",
}


def test_a_full_exit_asks_for_the_whole_holding_in_every_reader(market_home):
    """F-24: the engine archives a full exit as requested inf; runs orders, runs day, explain and runs show give null."""
    context = Context(market_home)
    run_id = backtest(BacktestRequest(strategy=EXITING), context).run_id
    sold = runs_orders(OrdersRequest(id=run_id, side="sell"), context).rows
    assert [(r.date, r.code, r.status, r.requested) for r in sold] == [
        (dt.date(2024, 1, 8), "000001.SZ", "filled", None)
    ]
    day = runs_day(RunDayRequest(id=run_id, date="2024-01-08"), context)
    found = explain(ExplainRequest(run=run_id, code="000001.SZ", date="2024-01-05"), context)
    assert [o.requested for o in day.orders if o.side == "sell"] == [None] == [o.requested for o in found.execution]
    for result in (day, found, runs_show(RunRequest(id=run_id), context)):
        assert type(result).model_validate_json(result.model_dump_json()) == result
