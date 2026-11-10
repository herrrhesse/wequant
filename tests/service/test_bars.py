from typing import get_args

import numpy as np
import pydantic
import pytest

from wequant.data.store import Store
from wequant.panel import MISSING, NOT_LISTED, RAW_FIELDS, SUSPENDED, TRADED, load_panel
from wequant.service import BarsRequest, Context, Exit, ServiceError, data_bars
from wequant.service.bars import BarField
from wequant.service.rows import STATUS_NAMES

FIELDS = get_args(BarField)

A, B = "600000.SH", "300001.SZ"


def expected(panel, field: str) -> np.ndarray:
    """field's grid read straight from the panel's accessors."""
    if field in RAW_FIELDS:
        return panel.raw(field)
    if field.startswith("adj_"):
        return panel.adjusted(field[4:])
    if field == "factor":
        return np.where(panel.traded, panel.factor, np.nan)
    if field.endswith("_price"):
        return getattr(panel, field)
    return panel.on_bars(getattr(panel, field))


def test_status_names_follow_the_panel_codes():
    names = STATUS_NAMES[[NOT_LISTED, TRADED, SUSPENDED, MISSING]]
    assert names.tolist() == ["not_listed", "traded", "suspended", "missing"]


@pytest.mark.parametrize("start, end", [(None, None), ("2024-01-04", "2024-01-09")])
def test_every_field_equals_load_panel(home, cache, context, start, end):
    result = data_bars(BarsRequest(codes=[B, A], start=start, end=end, fields=list(FIELDS)), context)
    panel = load_panel(start, end, [B, A], store=Store(home), cache_dir=cache)
    cells = [(n, t) for n in range(2) for t in range(len(panel.dates))]
    assert len(result.rows) == len(cells)
    for row, (n, t) in zip(result.rows, cells):
        assert (row.code, row.date) == (panel.codes[n], panel.dates[t].item())
        assert row.status == STATUS_NAMES[panel.status[t, n]]
        for field in FIELDS:
            want = expected(panel, field)[t, n]
            assert row.values[field] == (want if np.isfinite(want) else None), (field, row)


def test_exact_json(context):
    fields = ["close", "adj_close", "factor", "limit_up_price", "limit_known", "st"]
    request = BarsRequest(codes=[A], start="2024-01-04", end="2024-01-08", fields=fields)
    nulls = ",".join(f'"{f}":null' for f in fields)
    assert data_bars(request, context).model_dump_json() == (
        '{"data_version":1,"fields":["close","adj_close","factor","limit_up_price","limit_known","st"],"rows":['
        f'{{"date":"2024-01-04","code":"600000.SH","status":"missing","values":{{{nulls}}}}},'
        f'{{"date":"2024-01-05","code":"600000.SH","status":"suspended","values":{{{nulls}}}}},'
        '{"date":"2024-01-08","code":"600000.SH","status":"traded","values":{"close":5.1,"adj_close":10.2,'
        '"factor":2.0,"limit_up_price":5.5,"limit_known":1.0,"st":0.0}}]}'
    )


def test_exact_json_of_the_default_fields(context):
    request = BarsRequest(codes=[B, A], start="2024-01-04", end="2024-01-05")
    nulls = ",".join(f'"{f}":null' for f in request.fields)
    assert data_bars(request, context).model_dump_json() == (
        '{"data_version":1,"fields":["open","high","low","close","preclose","volume","amount","adj_close",'
        '"limit_up_price","limit_down_price"],"rows":['
        '{"date":"2024-01-04","code":"300001.SZ","status":"traded","values":{"open":20.0,"high":20.05,"low":19.95,'
        '"close":20.0,"preclose":null,"volume":100.0,"amount":1000.0,"adj_close":20.0,"limit_up_price":null,'
        '"limit_down_price":null}},'
        '{"date":"2024-01-05","code":"300001.SZ","status":"traded","values":{"open":21.0,"high":21.05,"low":20.95,'
        '"close":21.0,"preclose":20.0,"volume":100.0,"amount":1000.0,"adj_close":21.0,"limit_up_price":null,'
        '"limit_down_price":null}},'
        f'{{"date":"2024-01-04","code":"600000.SH","status":"missing","values":{{{nulls}}}}},'
        f'{{"date":"2024-01-05","code":"600000.SH","status":"suspended","values":{{{nulls}}}}}]}}'
    )


def test_free_and_unknown_limits_are_both_null_and_limit_known_tells_them_apart(context):
    fields = ["close", "limit_up_price", "limit_down_price", "limit_known"]
    rows = data_bars(BarsRequest(codes=[B], fields=fields), context).rows
    statuses = "not_listed not_listed traded traded suspended traded missing not_listed"
    assert [r.status for r in rows] == statuses.split()
    for row in rows:
        if row.status == "traded":
            assert row.values == {**row.values, "limit_up_price": None, "limit_down_price": None, "limit_known": 1.0}
    unknown = data_bars(BarsRequest(codes=[A], end="2024-01-02", fields=fields), context).rows
    assert unknown[0].values == {"close": 9.8, "limit_up_price": None, "limit_down_price": None, "limit_known": 0.0}


def test_a_nan_amount_comes_out_as_null_never_zero(home, cache, context):
    request = BarsRequest(codes=[A], end="2024-01-03", fields=["close", "amount"])
    assert data_bars(request, context).rows[1].values == {"close": 10.0, "amount": 1000.0}
    path = cache / "panel" / load_panel(store=Store(home), cache_dir=cache).key / "amount.npy"
    amount = np.load(path).copy()
    amount[1, 2] = np.nan  # 2024-01-03 of A: codes sort as D, B, A, C
    np.save(path, amount)
    result = data_bars(request, context)
    assert result.rows[1].model_dump(mode="json") == {
        "date": "2024-01-03",
        "code": A,
        "status": "traded",
        "values": {"close": 10.0, "amount": None},
    }
    text = result.model_dump_json()
    assert '"amount":null' in text and '"amount":0' not in text


def test_a_pinned_version(home_v2, cache):
    context = Context(home_v2, cache)
    request = {"codes": [A], "start": "2024-01-03", "end": "2024-01-03", "fields": ["close"]}
    assert data_bars(BarsRequest(**request, data_version=1), context).rows[0].values == {"close": 10.0}
    latest = data_bars(BarsRequest(**request), context)
    assert latest.data_version == 2 and latest.rows[0].values == {"close": 10.1}


@pytest.mark.parametrize(
    "fields",
    [
        {"codes": []},
        {"codes": [A, A]},
        {"codes": ["600000"]},
        {"codes": [A], "start": "2024-01-05", "end": "2024-01-02"},
        {"codes": [A], "fields": ["bogus"]},
        {"codes": [A], "fields": ["close", "close"]},
        {"codes": [A], "fields": []},
        {"codes": [A], "data_version": 0},
        {"codes": [A], "foo": 1},
    ],
)
def test_bad_requests_are_refused(fields):
    with pytest.raises(pydantic.ValidationError):
        BarsRequest(**fields)


@pytest.mark.parametrize(
    "request_, message",
    [
        ({"codes": ["600009.SH"]}, "unknown codes"),
        ({"codes": [A], "start": "2024-01-06", "end": "2024-01-07"}, "no trading day"),
        ({"codes": [A], "data_version": 9}, "store version 9 is not kept"),
    ],
)
def test_data_that_is_not_there(context, request_, message):
    with pytest.raises(ServiceError, match=message) as raised:
        data_bars(BarsRequest(**request_), context)
    assert raised.value.exit == Exit.NOT_FOUND
