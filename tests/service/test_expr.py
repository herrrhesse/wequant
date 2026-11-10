import json

import numpy as np
import pytest

from wequant.data.store import Store
from wequant.expr import evaluate, from_form
from wequant.panel import load_panel
from wequant.service import EvalRequest, Exit, ExprRequest, ServiceError, expr_check, expr_eval

A, B, D = "600000.SH", "300001.SZ", "000001.SZ"
GT = '["GT","close",["MA","close",2]]'


@pytest.mark.parametrize(
    "form, key, kind",
    [
        ('["MA","adj_close",2]', '["MA","adj_close",2]', "num"),
        (' [ "GT" , "close" , ["MA","close",2] ] ', '["GT","close",["MA","close",2]]', "bool"),
        ('["SUB",-0.0,"close"]', '["SUB",0.0,"close"]', "num"),
        ('"board@star"', '"board@star"', "bool"),
    ],
)
def test_a_valid_form_gives_its_canonical_key_and_kind(form, key, kind):
    result = expr_check(ExprRequest(form=form))
    assert result.model_dump_json() == json.dumps({"key": key, "kind": kind}, separators=(",", ":"))
    assert result.key == from_form(json.loads(form)).key


@pytest.mark.parametrize(
    "form, message",
    [
        ('["REF","close",-1]', "REF with -1 bars would read bars after day t"),
        ('"status"', "status is a descriptive day status that a later bar can change"),
        ('["IF","close",1,2]', "argument 0 of IF must be a condition, not the number close"),
        ('["MA","close",0]', "argument 1 of MA must be at least 1, not 0"),
        ('["NOPE","close"]', "unknown operator 'NOPE'"),
        ('{"a":1}', "not an expression form: {'a': 1}"),
        ("close", "the form is not JSON"),
        ("[" * 100_000, "the form is not JSON"),
    ],
)
def test_a_refused_form_is_a_usage_error(form, message):
    with pytest.raises(ServiceError) as raised:
        expr_check(ExprRequest(form=form))
    assert raised.value.exit == Exit.USAGE and str(raised.value).startswith(message)


def test_exact_json_with_warm_up(context):
    result = expr_eval(EvalRequest(form='["MA","close",2]', codes=[A], start="2024-01-08"), context)
    row = '{{"date":"2024-01-{}","code":"600000.SH","status":"traded","values":{{"value":{}}}}}'
    days = [("08", "7.55"), ("09", "5.15"), ("10", "5.1"), ("11", "5.25")]  # 7.55 averages the bar of 01-03
    assert result.model_dump_json() == (
        '{"data_version":1,"key":"[\\"MA\\",\\"close\\",2]","kind":"num","rows":['
        + ",".join(row.format(*day) for day in days)
        + "]}"
    )


def test_exact_json_of_a_condition(context):
    result = expr_eval(EvalRequest(form=GT, codes=[D], end="2024-01-05"), context)
    assert result.model_dump_json() == (
        '{"data_version":1,"key":"[\\"GT\\",\\"close\\",[\\"MA\\",\\"close\\",2]]","kind":"bool","rows":['
        '{"date":"2024-01-02","code":"000001.SZ","status":"traded","values":{"value":null}},'
        '{"date":"2024-01-03","code":"000001.SZ","status":"traded","values":{"value":1.0}},'
        '{"date":"2024-01-04","code":"000001.SZ","status":"suspended","values":{"value":null}},'
        '{"date":"2024-01-05","code":"000001.SZ","status":"traded","values":{"value":1.0}}]}'
    )


@pytest.mark.parametrize("form", ['["MA","adj_close",2]', '["CS_RANK","close"]', GT])
@pytest.mark.parametrize("start", [None, "2024-01-05"])
def test_values_equal_evaluate_on_the_full_history(home, cache, context, form, start):
    codes = [B, A, D]
    rows = expr_eval(EvalRequest(form=form, codes=codes, start=start), context).rows
    panel = load_panel(None, None, codes, store=Store(home), cache_dir=cache)
    first = 0 if start is None else panel.row(start)
    grid = evaluate(from_form(json.loads(form)), panel)[first:]
    cells = [(n, t) for n in range(len(codes)) for t in range(grid.shape[0])]
    assert [(r.code, r.date) for r in rows] == [(codes[n], panel.dates[first + t].item()) for n, t in cells]
    assert [r.values["value"] for r in rows] == [grid[t, n] if np.isfinite(grid[t, n]) else None for n, t in cells]


def test_a_value_never_depends_on_start(context):
    whole = expr_eval(EvalRequest(form='["MA","close",3]', codes=[A]), context).rows
    tail = expr_eval(EvalRequest(form='["MA","close",3]', codes=[A], start="2024-01-08"), context).rows
    assert tail == whole[-len(tail) :] and len(tail) == 4


@pytest.mark.parametrize(
    "request_, exit, message",
    [
        ({"form": '["REF","close",-1]'}, Exit.USAGE, "would read bars after day t"),
        ({"form": "close"}, Exit.USAGE, "the form is not JSON"),
        ({"form": '"close"', "codes": ["600009.SH"]}, Exit.NOT_FOUND, "unknown codes"),
        ({"form": '"close"', "start": "2024-01-12"}, Exit.NOT_FOUND, "no trading day"),
        ({"form": '"close"', "end": "2023-12-01"}, Exit.NOT_FOUND, "no trading day"),
        ({"form": '"close"', "data_version": 9}, Exit.NOT_FOUND, "store version 9 is not kept"),
    ],
)
def test_errors(context, request_, exit, message):
    with pytest.raises(ServiceError, match=message) as raised:
        expr_eval(EvalRequest(**{"codes": [A], **request_}), context)
    assert raised.value.exit == exit
