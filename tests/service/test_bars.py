from typing import get_args

import numpy as np
import pydantic
import pytest

from tests.test_panel import BARS, bar, seed
from wequant.data.store import Store
from wequant.panel import RAW_FIELDS, load_panel
from wequant.service import BarsRequest, Context, data_bars
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


def test_every_field_equals_load_panel(home, cache, context):
    """The service has no logic of its own: every field of every cell is the panel's (slicing: tests.test_panel)."""
    result = data_bars(BarsRequest(codes=[B, A], fields=list(FIELDS)), context)
    panel = load_panel(None, None, [B, A], store=Store(home), cache_dir=cache)
    cells = [(n, t) for n in range(2) for t in range(len(panel.dates))]
    assert len(result.rows) == len(cells)
    for row, (n, t) in zip(result.rows, cells):
        assert (row.code, row.date) == (panel.codes[n], panel.dates[t].item())
        assert row.status == STATUS_NAMES[panel.status[t, n]]
        for field in FIELDS:
            want = expected(panel, field)[t, n]
            assert row.values[field] == (want if np.isfinite(want) else None), (field, row)


def test_exact_json(context):
    """The wire format of data bars: missing and suspended days carry null values."""
    fields = ["close", "adj_close", "factor", "limit_up_price", "limit_known", "st"]
    request = BarsRequest(codes=[A], start="2024-01-04", end="2024-01-08", fields=fields)
    nulls = ",".join(f'"{f}":null' for f in fields)
    day = Store(context.home).manifest()["created_at"][:10].replace("-", "")  # no last trade date: its creation day
    assert data_bars(request, context).model_dump_json() == (
        f'{{"data_version":1,"data_version_name":"{day}.1",'
        '"fields":["close","adj_close","factor","limit_up_price","limit_known","st"],'
        '"names":{"600000.SH":null},"rows":['
        f'{{"date":"2024-01-04","code":"600000.SH","status":"missing","values":{{{nulls}}}}},'
        f'{{"date":"2024-01-05","code":"600000.SH","status":"suspended","values":{{{nulls}}}}},'
        '{"date":"2024-01-08","code":"600000.SH","status":"traded","values":{"close":5.1,"adj_close":10.2,'
        '"factor":2.0,"limit_up_price":5.5,"limit_known":1.0,"st":0.0}}]}'
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


def test_a_null_amount_comes_out_as_null_never_zero(tmp_path, cache):
    """a0a1ba9: a traded bar whose amount a resolution voided reads as null, never as 0."""
    voided = bar(A, 2, 10000, 9800)
    home = seed(tmp_path / "voided", bars=[{**row, "amount": None} if row == voided else row for row in BARS]).home
    request = BarsRequest(codes=[A], start="2024-01-03", end="2024-01-03", fields=["close", "amount"])
    result = data_bars(request, Context(home, cache))
    assert [(r.status, r.values) for r in result.rows] == [("traded", {"close": 10.0, "amount": None})]
    assert '"amount":null' in result.model_dump_json()


def test_a_pinned_version(home_v2, cache):
    """data_version reads that store version, not the current one."""
    context = Context(home_v2, cache)
    request = {"codes": [A], "start": "2024-01-03", "end": "2024-01-03", "fields": ["close"]}
    assert data_bars(BarsRequest(**request, data_version=1), context).rows[0].values == {"close": 10.0}
    latest = data_bars(BarsRequest(**request), context)
    assert latest.data_version == 2 and latest.rows[0].values == {"close": 10.1}


@pytest.mark.parametrize(
    "fields",
    [
        {"codes": [A, A]},
        {"codes": ["600000"]},
        {"codes": [A], "start": "2024-01-05", "end": "2024-01-02"},
    ],
    ids=["codes repeat", "code without suffix", "start after end"],
)
def test_bad_requests_are_refused(fields):
    """The request's own validators: distinct codes, codes with their exchange suffix, start <= end."""
    with pytest.raises(pydantic.ValidationError):
        BarsRequest(**fields)
