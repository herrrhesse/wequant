"""S3.10: every rule a user can break carries a Chinese hint of its own, not just its code's general sentence."""

import pytest

from tests.service.conftest import DAYS, SCREENED
from wequant.data.store import Store
from wequant.service import (
    BarsRequest,
    Context,
    DecisionRequest,
    IndexPoolRequest,
    PoolSaveRequest,
    ServiceError,
    SettingsUpdate,
    StatusRequest,
    StrategyRequest,
    data_bars,
    data_resolve_approve,
    data_status,
    pools_from_index,
    pools_save,
    runs_rerun,
)
from wequant.service.backtest import BacktestRequest, backtest
from wequant.service.details import to_data, to_execution, to_strategy, validated
from wequant.service.explain import ExplainRequest, explain
from wequant.service.expr import EvalRequest, expr_eval
from wequant.service.labels import ERROR_CODES
from wequant.service.runs import RunDayRequest, RunRequest, runs_day
from wequant.service.screen import ScreenRequest, screen
from wequant.service.specs import DataSpec, ExecutionSpec, MetricSpecModel
from wequant.service.transfer import receive

DAILY = {**SCREENED, "period": "day"}
FACTOR = {"name": "f", "expr": "close"}
CODE = "000001.SZ"


def strategy(**fields):
    """The service's check of an inline strategy: pydantic's fields first, then the core's rules relating them."""
    return lambda: to_strategy(validated(StrategyRequest, {"strategy": fields}).strategy)


def factor(expr):
    return strategy(factors=[{"name": "f", "expr": expr}])


RULES = {  # rule -> a request that breaks it and nothing else
    "requires top_n": strategy(top_n=5),
    "requires backups": strategy(backups=1),
    "requires weighting": strategy(weighting="score"),
    "requires formula": strategy(formula="close"),
    "requires hold_rank": strategy(hold_rank=5),
    "requires failure_recovery": lambda: to_execution(ExecutionSpec(delisting="hold", failure_recovery=0.5)),
    "requires exclude_suspicious": lambda: to_data(DataSpec(exclude_suspicious=True)),
    "requires strategy or saved": lambda: validated(StrategyRequest, {}),
    "requires strategy, saved or run": lambda: validated(ExplainRequest, {"code": CODE, "date": DAYS[-1]}),
    "requires a selector": lambda: data_resolve_approve(DecisionRequest(note="n"), Context()),
    "requires one scope": strategy(pool={"index": "000300.SH", "stocks": {"codes": ["600000.SH"]}}),
    "out_of_range top_n": strategy(top_n=0),
    "out_of_range backups": strategy(backups=-1),
    "out_of_range every": strategy(every=0),
    "out_of_range band": strategy(band=-0.1),
    "out_of_range hold_rank": strategy(hold_rank=0),
    "out_of_range hold_rank above top_n": strategy(factors=[FACTOR], top_n=5, hold_rank=5),
    "out_of_range max_weight": strategy(max_weight=1.0),
    "out_of_range boards": strategy(pool={"boards": []}),
    "out_of_range min_list_days": strategy(pool={"min_list_days": 1}),
    "out_of_range weight": strategy(factors=[{**FACTOR, "weight": 0.0}]),
    "out_of_range conditions": strategy(filters=[{"name": "g", "conditions": []}]),
    "wrong_type empty name": strategy(factors=[{**FACTOR, "name": ""}]),
    "out_of_range commission_rate": lambda: validated(ExecutionSpec, {"commission_rate": 1.0}),
    "out_of_range min_commission": lambda: validated(ExecutionSpec, {"min_commission": -1.0}),
    "out_of_range min_commission in fen": lambda: to_execution(ExecutionSpec(min_commission=5.001)),
    "out_of_range slippage_bps": lambda: validated(ExecutionSpec, {"slippage_bps": 10000.0}),
    "out_of_range failure_recovery": lambda: validated(ExecutionSpec, {"failure_recovery": 1.5}),
    "out_of_range cash": lambda: validated(BacktestRequest, {"strategy": DAILY, "cash": 0.0}),
    "out_of_range periods_per_year": lambda: validated(MetricSpecModel, {"periods_per_year": 0}),
    "out_of_range tail_fraction": lambda: validated(MetricSpecModel, {"tail_fraction": 1.0}),
    "out_of_range runs_limit_gb": lambda: validated(SettingsUpdate, {"runs_limit_gb": 0.0}),
    "out_of_range keep_versions": lambda: validated(SettingsUpdate, {"keep_versions": 0}),
    "out_of_range codes": lambda: validated(BarsRequest, {"codes": []}),
    "out_of_range fields": lambda: validated(BarsRequest, {"codes": [CODE], "fields": []}),
    "out_of_range start after end": lambda: validated(BarsRequest, {"codes": [CODE], "start": DAYS[3], "end": DAYS[1]}),
    "out_of_range a window too short": factor(["MA", "close", 0]),
    "out_of_range a window too long": factor(["MA", "close", 2**62 + 1]),
    "out_of_range a weight above the window": factor(["SMA", "close", 5, 6]),
    "not_available turnover": factor("turn"),
    "wrong_type date": lambda: validated(BarsRequest, {"codes": [CODE], "start": "2024/01/02"}),
    "wrong_type code": lambda: validated(BarsRequest, {"codes": ["000001"]}),
}


def run(home, **request):
    return backtest(BacktestRequest(**{"strategy": DAILY, **request}), Context(home))


def gone(home) -> str:
    """A run whose store version the store no longer keeps."""
    run_id = run(home).run_id
    store = Store(home)
    store.commit({"note": "later"})
    store.commit({"note": "later still"})
    store.cleanup(keep=1, dry_run=False)
    return run_id


def explained(home, **request):
    return explain(ExplainRequest(**{"code": CODE, "date": DAYS[-1], **request}), Context(home))


ON_DATA = {  # rule -> a call on tests.service.conftest's synthetic market that breaks it
    "requires data from the run": lambda h: explained(h, run=run(h).run_id, data={}),
    "requires data_version from the run": lambda h: explained(h, run=run(h).run_id, data_version=1),
    "out_of_range start before the data": lambda h: run(h, start="2023-12-29"),
    "out_of_range start before the anchor": lambda h: run(h, strategy={**DAILY, "anchor": DAYS[1]}, start=DAYS[0]),
    "out_of_range no run day": lambda h: run(h, start=DAYS[-1], end=DAYS[-1]),
    "out_of_range cash in fen": lambda h: run(h, cash=1000.001),
    "not_a_trading_day anchor": lambda h: run(h, strategy={**DAILY, "anchor": "2024-01-06"}),
    "not_a_trading_day screen date": lambda h: screen(ScreenRequest(strategy=SCREENED, date="2024-01-06"), Context(h)),
    "not_a_trading_day run date": lambda h: runs_day(RunDayRequest(id=run(h).run_id, date="2024-01-06"), Context(h)),
    "not_a_trading_day expression range": lambda h: expr_eval(
        EvalRequest(form='"close"', codes=[CODE], start="2024-02-01"), Context(h)
    ),
    "not_a_trading_day bars range": lambda h: data_bars(
        BarsRequest(codes=[CODE], start="2024-01-06", end="2024-01-07"), Context(h)
    ),
    "no_data benchmark": lambda h: run(h, benchmark="000905.SH"),
    "wrong_type benchmark": lambda h: run(h, benchmark="300"),
    "version_not_kept data_version": lambda h: data_status(StatusRequest(data_version=9), Context(h)),
    "version_not_kept rerun": lambda h: runs_rerun(RunRequest(id=gone(h)), Context(h)),
    "version_not_kept explain": lambda h: explained(h, run=gone(h)),
    "bad_file name": lambda h: receive(Context(h), "picked.txt", b""),
    "no_data index members": lambda h: pools_from_index(IndexPoolRequest(index="000300.SH"), Context(h)),
    "out_of_range blank pool name": lambda h: pools_save(PoolSaveRequest(name=" ", codes=[]), Context(h)),
}


def assert_hinted(call):
    with pytest.raises(ServiceError) as raised:
        call()
    [detail] = raised.value.detail
    assert detail.hint and detail.hint != ERROR_CODES[detail.code][1], detail


@pytest.mark.parametrize("call", RULES.values(), ids=RULES)
def test_a_broken_rule_has_a_hint_of_its_own(call):
    assert_hinted(call)


@pytest.mark.parametrize("call", ON_DATA.values(), ids=ON_DATA)
def test_a_rule_broken_on_data_has_a_hint_of_its_own(market_home, call):
    assert_hinted(lambda: call(market_home))
