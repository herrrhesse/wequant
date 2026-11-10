import json

import numpy as np
import pytest

from wequant.data.store import Store
from wequant.expr import evaluate, from_form
from wequant.panel import load_panel
from wequant.service import EvalRequest, Exit, ExprRequest, ServiceError, expr_check, expr_eval

A, B, D = "600000.SH", "300001.SZ", "000001.SZ"


def test_a_valid_form_gives_its_canonical_key_and_kind():
    """expr check passes on the expression layer's key and kind (canonicalisation: tests/expr/test_tree.py)."""
    result = expr_check(ExprRequest(form='["MA","adj_close",2]'))
    assert result.model_dump_json() == '{"key":"[\\"MA\\",\\"adj_close\\",2]","kind":"num"}'
    assert result.key == from_form(["MA", "adj_close", 2]).key


@pytest.mark.parametrize(
    "form, code, path",
    [
        ('["REF","close",-1]', "negative_window", [2]),
        ("close", "not_json", []),
        ("[" * 100_000, "not_json", []),  # RecursionError while parsing
    ],
    ids=["refused form", "not JSON", "nested too deep"],
)
def test_a_refused_form_is_a_usage_error(form, code, path):
    """USAGE with the expression's code and the path to the fault inside the form."""
    with pytest.raises(ServiceError) as raised:
        expr_check(ExprRequest(form=form))
    assert raised.value.exit == Exit.USAGE and [(d.code, d.path) for d in raised.value.detail] == [(code, path)]


def test_values_equal_evaluate_on_the_full_history(home, cache, context):
    """The service has no logic of its own: each cell is evaluate's on the panel from the first trading day."""
    form, codes = '["MA","adj_close",2]', [B, A, D]
    rows = expr_eval(EvalRequest(form=form, codes=codes), context).rows
    panel = load_panel(None, None, codes, store=Store(home), cache_dir=cache)
    grid = evaluate(from_form(json.loads(form)), panel)
    cells = [(n, t) for n in range(len(codes)) for t in range(grid.shape[0])]
    assert [(r.code, r.date) for r in rows] == [(codes[n], panel.dates[t].item()) for n, t in cells]
    assert [r.values["value"] for r in rows] == [grid[t, n] if np.isfinite(grid[t, n]) else None for n, t in cells]


def test_a_value_never_depends_on_start(context):
    """Warm-up: a window reads the bars before start, so a day's value is the same whatever the start."""
    whole = expr_eval(EvalRequest(form='["MA","close",2]', codes=[A]), context).rows
    tail = expr_eval(EvalRequest(form='["MA","close",2]', codes=[A], start="2024-01-08"), context).rows
    assert tail == whole[-len(tail) :]
    assert [(str(r.date), r.values["value"]) for r in tail] == [
        ("2024-01-08", 7.55),  # with the close of 2024-01-03 (10.00): the days between have no bar
        ("2024-01-09", 5.15),
        ("2024-01-10", 5.1),
        ("2024-01-11", 5.25),
    ]


def test_a_start_after_the_last_day_is_not_found(context):
    """expr eval's own NOT_FOUND: no trading day on or after start."""
    with pytest.raises(ServiceError) as raised:
        expr_eval(EvalRequest(form='"close"', codes=[A], start="2024-01-12"), context)
    assert raised.value.exit == Exit.NOT_FOUND
    assert [(d.code, d.loc) for d in raised.value.detail] == [("not_a_trading_day", ["start"])]
