"""The Run record: canonical JSON, run ids and the checks a run passes when it is built."""

import datetime as dt

import polars as pl
import pytest

from tests.account.fake import FakePanel
from wequant.account import Account, Execution
from wequant.analytics import MetricSpec, compute_metrics
from wequant.expr import panel_key
from wequant.runs import FORMAT, Run, canonical, run_id, run_inputs

GOLDEN = {"top": 50, "rate": 0.0003, "codes": ["600000.SH"]}


def golden_run(**changes) -> Run:
    """Buy 1,000 600000.SH on 2024-01-03 and hold; FakePanel data, store version 1."""
    panel = FakePanel(
        ["2024-01-02", "2024-01-03", "2024-01-04"], ["600000.SH"], ["main"], [10.0, 10.0, 10.4], [10.0, 10.5, 10.2]
    )
    account = Account(panel, 100_000, "2024-01-02", Execution(slippage_bps=0.0))
    for t in (1, 2):
        account.begin(t)
        if t == 1:
            account.trade([0], [1000])
        account.end()
    ledger = account.ledger()
    metrics = compute_metrics(
        ledger.daily, pl.Series("date", panel.dates), positions=ledger.positions, orders=ledger.orders
    )
    fields = {
        "engine": "hold",
        "description": {"code": "600000.SH", "shares": 1000},
        "versions": {"hold": 1},
        "panel_key": panel_key(panel),
        "anchor": dt.date(2024, 1, 2),
        "ledger": ledger,
        "metrics": metrics,
        "spec": MetricSpec(),
        "picks": pl.DataFrame(),
        "funnel": pl.DataFrame(),
        "detail": pl.DataFrame(),
        "warnings": {},
    }
    return Run(**(fields | changes))


def test_canonical_text_and_run_id_golden():
    assert canonical(GOLDEN) == '{"codes":["600000.SH"],"rate":0.0003,"top":50}'
    assert run_id(GOLDEN) == "33fdee74ed1ccb56"
    assert run_id(dict(reversed(GOLDEN.items()))) == "33fdee74ed1ccb56"
    assert run_id(GOLDEN | {"top": 50.0}) != run_id(GOLDEN)


@pytest.mark.parametrize("value", [(1, 2), {1: "a"}, float("nan"), float("inf"), dt.date(2024, 1, 2)])
def test_canonical_refuses_what_is_not_plain_json(value):
    with pytest.raises(ValueError, match="not plain JSON"):
        canonical({"x": value})


def test_inputs_and_id():
    run = golden_run()
    assert run.inputs == {
        "format": FORMAT,
        "engine": "hold",
        "description": {"code": "600000.SH", "shares": 1000},
        "store_version": 1,
        "data_key": "fake",
        "rules_key": "fake-r1",
        "panel_key": "fake-fake-r1-2024-01-02-2024-01-04-56dab30ebda9",
        "versions": {"hold": 1},
    }
    assert run.id == run_id(run.inputs) == "c43c01570b643d1f"
    assert run.store_version == 1 and run.code_version == run.ledger.meta["code_version"]
    assert golden_run(description={"shares": 1000, "code": "600000.SH"}).id == run.id


def test_the_id_is_known_before_the_run_and_names_its_panel():
    run = golden_run()
    before = run_inputs(
        "hold",
        {"code": "600000.SH", "shares": 1000},
        {"hold": 1},
        store_version=1,
        data_key="fake",
        rules_key="fake-r1",
        panel_key=run.panel_key,
    )
    assert before == run.inputs
    assert golden_run(panel_key=run.panel_key.replace("2024-01-02", "2023-12-29")).id != run.id


def test_runs_compare_by_identity():
    run = golden_run()
    assert run == run and run != golden_run() and len({run, run}) == 1


def test_run_keeps_its_own_copy_of_the_description():
    description = {"code": "600000.SH", "shares": 1000}
    run = golden_run(description=description)
    description["shares"] = 2000
    assert run.description["shares"] == 1000


@pytest.mark.parametrize(
    "changes",
    [
        {"description": [1]},
        {"description": {"x": (1, 2)}},
        {"versions": {"hold": 1.0}},
        {"warnings": {"Bad": "x"}},
        {"warnings": {"ok": 1}},
    ],
)
def test_run_refusals(changes):
    with pytest.raises(ValueError):
        golden_run(**changes)
