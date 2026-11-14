"""Leaving out stocks in their delisting period (S2.10a): a funnel step of its own, the day of the period in the
decision chain, a warning on data without names, templates on and saved strategies as they were."""

import datetime as dt

import polars as pl

from tests.backtest.market import build_store, business_days
from wequant import service
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.data.store import Store
from wequant.panel import load_panel
from wequant.service import Context
from wequant.strategy import Strategy

A, B = "600000.SH", "600001.SH"
DAYS = business_days(20, "2024-01-02")
TOP = {"factors": [{"name": "close", "expr": "close"}], "top_n": 1, "period": "day"}  # the dearer stock: A


def home(tmp_path, names=True):
    """A at 30 yuan delisted after its last bar on day 20, named 甲退; B at 20 yuan."""
    store = build_store(tmp_path / "home", DAYS, {A: [(30.0, 30.0)] * 20, B: [(20.0, 20.0)] * 20})
    if names:
        delisted = {A: dt.date.fromisoformat(DAYS[-1]) + dt.timedelta(days=3)}
        securities = store.read_table("securities").with_columns(
            name=pl.col("code").replace_strict({A: "甲退", B: "乙股份"}),
            delist_date=pl.col("code").replace_strict(delisted, default=None, return_dtype=pl.Date),
        )
        store.commit({}, replace={"securities": securities}, reason="unspecified")
    return store.home


def screen(home_dir, cache, pool, day=DAYS[-1]):
    strategy = TOP | {"pool": pool}
    return service.screen(service.ScreenRequest(strategy=strategy, date=day), Context(home_dir, cache))


def picked(found) -> list[str]:
    return [row.code for row in found.rows if row.role == "target"]


def test_a_stock_in_its_delisting_period_is_left_out_at_its_own_funnel_step(tmp_path, cache):
    found = screen(home(tmp_path), cache, {"exclude_delisting": True})
    step = next(s for s in found.funnel if s.step == "exclude_delisting")
    assert (step.kind, step.left, picked(found)) == ("pool", 1, [B])
    assert picked(screen(home(tmp_path / "off"), cache, {})) == [A]


def test_the_decision_chain_says_which_day_of_the_period_it_is(tmp_path, cache):
    request = service.ExplainRequest(strategy=TOP | {"pool": {"exclude_delisting": True}}, code=A, date=DAYS[-2])
    check = next(
        c for c in service.explain(request, Context(home(tmp_path), cache)).pool if c.name == "exclude_delisting"
    )
    assert (check.passed, check.value, check.note) == (False, "14/15", "处于退市整理期（第 14/15 天）")


def test_data_without_names_warns_that_the_period_cannot_be_told(tmp_path, cache):
    found = screen(home(tmp_path, names=False), cache, {"exclude_delisting": True})
    assert "delisting_unknown" in [w.code for w in found.warnings] and picked(found) == [A]


def test_every_template_leaves_out_the_delisting_period_and_a_saved_form_without_it_keeps_it_off():
    templates = service.strategy_templates(service.TemplatesRequest()).rows
    assert all(Strategy.from_form(t.strategy.model_dump(exclude_none=True)).pool.exclude_delisting for t in templates)
    saved = {"pool": {"exclude_st": True}, **TOP}
    assert Strategy.from_form(saved).form == saved and not Strategy.from_form(saved).pool.exclude_delisting


def test_a_holding_entering_its_delisting_period_is_sold_at_the_next_rebalance(tmp_path, cache):
    """As when a stock leaves the scope: the decision on the period's first day drops it (S2.10a)."""
    panel = load_panel(store=Store(home(tmp_path)), cache_dir=cache)
    strategy = Strategy.from_form(TOP | {"pool": {"exclude_delisting": True}})
    orders = run(Backtest(strategy, execution=Execution(slippage_bps=0.0), benchmark=None), panel).ledger.orders
    sold = orders.filter((pl.col("code") == A) & (pl.col("side") == "sell"))["date"].to_list()
    first = dt.date.fromisoformat(DAYS[5])  # the period's first day: the last 15 of A's 20 bars
    assert sold == [dt.date.fromisoformat(DAYS[6])] and first < sold[0]  # filled the day after that decision


def test_a_screen_s_reject_in_its_delisting_period_carries_the_decision_chain_s_note(tmp_path, cache):
    found = screen(home(tmp_path), cache, {"exclude_delisting": True})
    reject = next(r for r in found.rejects if r.code == A)
    assert (reject.step, reject.note) == ("exclude_delisting", "处于退市整理期（第 15/15 天）")
