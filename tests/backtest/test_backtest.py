"""Backtest descriptions: validation, the panel they expect, their run window, and runs from a canonical form."""

import datetime as dt
import math
from types import SimpleNamespace

import numpy as np
import pytest

from tests.backtest.market import CSI300, ROTATION, WEEK, A, build_store, business_days
from wequant.account import Execution
from wequant.analytics import MetricSpec
from wequant.backtest import Backtest, panel_for, planned_id, run, run_form, span
from wequant.expr import Field
from wequant.panel import load_panel
from wequant.strategy import Factor, SpecError, Strategy

EX0 = Execution(slippage_bps=0.0)
CHEAP = Strategy(factors=(Factor("cheap", Field("close"), prefer="low"),), top_n=1, period="day")
JAN = {n: dt.date(2024, 1, n) for n in range(1, 32)}


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"start": JAN[5], "end": JAN[4]}, "start"),
        ({"strategy": Strategy(anchor=JAN[3]), "start": JAN[2]}, "start"),
        ({"start": dt.datetime(2024, 1, 2)}, "start"),
        ({"end": "2024-01-02"}, "end"),
        ({"cash": 0.001}, "cash"),
        ({"cash": 0}, "cash"),
        ({"cash": -100.0}, "cash"),
        ({"cash": math.nan}, "cash"),
        ({"cash": True}, "cash"),
        ({"cash": 10**400}, "cash"),
        ({"benchmark": ""}, "benchmark"),
        ({"benchmark": 300}, "benchmark"),
        ({"strategy": {"top_n": 1}}, "strategy"),
        ({"execution": {"price": "open"}}, "execution"),
        ({"metrics": None}, "metrics"),
    ],
)
def test_backtest_validation_names_the_field(changes, field):
    with pytest.raises(ValueError, match=rf"^{field}[: ]"):
        Backtest(**({"strategy": CHEAP} | changes))


def test_cash_is_kept_as_a_float():
    assert type(Backtest(CHEAP, cash=100_000).cash) is float and Backtest(CHEAP, cash=0.01).cash == 0.01
    assert Backtest(CHEAP).execution == Execution() and Backtest(CHEAP).metrics == MetricSpec()


def stand_in(days):
    return SimpleNamespace(dates=np.array(days, dtype="datetime64[D]"))


TEN = stand_in(business_days(10))  # 2024-01-02 (Tue) .. 2024-01-15 (Mon)


@pytest.mark.parametrize(
    "start, end, rows",
    [
        (None, None, (0, 9)),
        (JAN[6], None, (4, 9)),  # Saturday: the next trading day
        (JAN[2], JAN[7], (0, 3)),  # Sunday: the last trading day before it
        (JAN[3], JAN[5], (1, 3)),
    ],
)
def test_span_snaps_to_trading_days(start, end, rows):
    assert span(Backtest(CHEAP, start=start, end=end), TEN) == rows


@pytest.mark.parametrize(
    "backtest, code, loc",
    [
        (Backtest(CHEAP, start=dt.date(2024, 1, 1)), "out_of_range", ("start",)),
        (Backtest(Strategy(anchor=JAN[3])), "not_a_trading_day", ("anchor",)),
        (Backtest(CHEAP, start=JAN[15]), "out_of_range", ("end",)),
        (Backtest(CHEAP, start=JAN[20]), "out_of_range", ("end",)),
        (Backtest(CHEAP, start=JAN[13], end=JAN[14]), "out_of_range", ("end",)),
    ],
)
def test_span_refusals(backtest, code, loc):
    with pytest.raises(SpecError, match=f"^{loc[0]}: ") as raised:
        span(backtest, TEN)
    assert (raised.value.code, raised.value.loc) == (code, loc)


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    home = tmp_path_factory.mktemp("rotation")
    return build_store(home / "home", WEEK, ROTATION, index=CSI300), home / "cache"


def test_panel_for_loads_every_code_from_the_anchor_to_end(store):
    store, cache = store
    panel = panel_for(Backtest(Strategy(anchor=JAN[3]), end=JAN[5]), store=store, cache_dir=cache)
    assert panel.dates.astype(str).tolist() == WEEK[1:4] and panel.codes.tolist() == ["000001.SZ", A]
    assert panel_for(Backtest(CHEAP), store=store, version=1, cache_dir=cache).dates.astype(str).tolist() == WEEK


def test_run_form_is_run_on_panel_for(store):
    store, cache = store
    backtest = Backtest(CHEAP, cash=100_000, execution=EX0)
    direct = run(backtest, panel_for(backtest, store=store, cache_dir=cache))
    again = run_form(backtest.form, 1, store=store, cache_dir=cache)
    assert again.id == direct.id == planned_id(backtest, panel_for(backtest, store=store, cache_dir=cache))
    for name in ("picks", "funnel", "detail"):
        assert getattr(again, name).equals(getattr(direct, name))
    for name in ("daily", "positions", "orders"):
        assert getattr(again.ledger, name).equals(getattr(direct.ledger, name))
    assert again.description == backtest.form and not again.ledger.orders.is_empty()


def test_one_form_on_two_one_code_panels_gets_two_ids(store):
    store, cache = store
    backtest = Backtest(CHEAP, cash=100_000, execution=EX0, benchmark=None)
    ids = {run(backtest, load_panel(codes=[code], store=store, cache_dir=cache)).id for code in ROTATION}
    assert len(ids) == 2
