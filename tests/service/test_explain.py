"""explain on the synthetic market (tests.service.conftest.MARKET): the same cross-section as screen, and with a run the
archived decisions and fills."""

import datetime as dt

import pytest

from tests.service.conftest import DAYS, MARKET, SCREENED
from wequant.service import Context, Exit, ServiceError
from wequant.service.backtest import BacktestRequest, backtest
from wequant.service.explain import ExplainRequest, explain
from wequant.service.runs import OrdersRequest, RunDayRequest, runs_day, runs_orders
from wequant.service.screen import ScreenRequest, screen

LAST = DAYS[-1]


def explained(home, code, **request):
    return explain(ExplainRequest(**{"strategy": SCREENED, "code": code, "date": LAST, **request}), Context(home))


def test_every_listed_stock_is_explained_as_screen_shows_it(market_home):
    """A scored stock's score, factors, role and weights are its screen row; a reject's first failed check is its
    screen reject, with the same value."""
    shown = screen(ScreenRequest(strategy=SCREENED, date=LAST), Context(market_home))
    rows, rejects = {r.code: r for r in shown.rows}, {r.code: r for r in shown.rejects}
    for code in MARKET:
        found = explained(market_home, code)
        if code in rows:
            row = rows[code]
            got = (found.score.rank, found.score.score, found.role, found.weight, found.raw_weight)
            assert got == (row.rank, row.score, row.role, row.weight, row.raw_weight)
            assert [c.model_dump() for c in row.factors] == [
                f.model_dump(exclude={"prefer", "weight"}) for f in found.score.factors
            ]
        else:
            checks = [*found.pool, *(s for s in found.steps if s.chain)]
            first = next(c for c in checks if not c.passed)
            assert (found.score, first.name) == (None, rejects[code].step)
            if rejects[code].kind == "condition":
                assert (first.value, first.op, first.threshold) == (rejects[code].value, "lt", 30.0)


def test_each_pool_check_shows_what_it_read(market_home):
    pooled = {**SCREENED, "pool": {"boards": ["main"], "exclude_st": True, "min_list_days": 5}}
    pool = explained(market_home, "600004.SH", strategy=pooled).pool
    assert [(p.name, p.passed, p.value) for p in pool] == [
        ("bar", True, pytest.approx(10.1)),  # the raw close of the day
        ("universe", True, None),
        ("boards", True, "main"),
        ("exclude_st", True, None),  # no daily_status row: ST unknown, read as not ST
        ("min_list_days", True, 8),
    ]
    assert pool[-1].note is not None


def test_with_a_run_the_role_holding_action_and_fills_are_the_archives(market_home):
    context = Context(market_home)
    run_id = backtest(BacktestRequest(saved={"id": "m"}), context).run_id
    decided = {d.code: d for d in runs_day(RunDayRequest(id=run_id, date="2024-01-05"), context).decisions}
    filled = runs_orders(OrdersRequest(id=run_id, code="600004.SH", start="2024-01-08", end="2024-01-08"), context).rows
    for day in ("2024-01-05", "2024-01-08"):  # the decision day and its fill day explain the same decision
        found = explain(ExplainRequest(run=run_id, code="600004.SH", date=day), context)
        row = decided["600004.SH"]
        assert (found.date, found.next_day) == (dt.date(2024, 1, 5), dt.date(2024, 1, 8))
        assert (found.role, found.weight, found.held, found.action) == (row.role, row.weight, row.held, row.action)
        assert found.execution == filled and found.run_id == run_id


FAILURES = [
    ({"run": "{run}", "strategy": None, "date": "2024-01-09"}, Exit.USAGE, "not_a_decision_day", ["date"]),
    ({"run": "{run}", "strategy": None, "data_version": 1}, Exit.USAGE, "requires", ["data_version"]),
    ({"run": "0" * 16, "strategy": None}, Exit.NOT_FOUND, "unknown_run", ["run"]),
    ({"code": "600009.SH"}, Exit.NOT_FOUND, "unknown_code", ["code"]),
]


@pytest.mark.parametrize("request_, exit, code, loc", FAILURES, ids=[f"{case[2]} {case[3]}" for case in FAILURES])
def test_what_explain_refuses(market_home, request_, exit, code, loc):
    run_id = backtest(BacktestRequest(saved={"id": "m"}, save=True), Context(market_home)).run_id
    fields = {key: value.format(run=run_id) if isinstance(value, str) else value for key, value in request_.items()}
    with pytest.raises(ServiceError) as raised:
        explained(market_home, fields.pop("code", "600004.SH"), **{"date": "2024-01-05", **fields})
    assert raised.value.exit == exit and [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]
