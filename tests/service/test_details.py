"""Every failure carries located details with stable codes (design 7.3): pydantic's, the core's, and NOT_FOUND's."""

import pytest

from wequant.service import (
    BarsRequest,
    Context,
    Exit,
    ServiceError,
    StatusRequest,
    StrategyRequest,
    data_bars,
    data_status,
)
from wequant.service.details import to_strategy, validated
from wequant.service.specs import StrategySpec

FACTOR = {"name": "f", "expr": "close"}
REQUESTS = [
    ({"strategy": {"zz": 1}}, "unknown_key", ["strategy", "zz"]),
    ({"strategy": {"factors": [{"expr": "close"}]}}, "missing_key", ["strategy", "factors", 0, "name"]),
    ({"strategy": {"period": "year"}}, "not_a_choice", ["strategy", "period"]),
    ({"strategy": {"top_n": 0}}, "out_of_range", ["strategy", "top_n"]),
    ({"strategy": {"pool": {"boards": []}}}, "out_of_range", ["strategy", "pool", "boards"]),
    ({"strategy": {"top_n": "5"}}, "wrong_type", ["strategy", "top_n"]),
    ({"strategy": {"anchor": "2024-13-01"}}, "wrong_type", ["strategy", "anchor"]),
    ({}, "requires", []),
    (
        {"strategy": {"filters": [{"name": "g", "conditions": [{"name": "c", "expr": 5}]}]}},
        "not_a_form",
        ["strategy", "filters", 0, "conditions", 0, "expr"],
    ),
    ({"strategy": {"filters": [7]}}, "wrong_type", ["strategy", "filters", 0]),
]


@pytest.mark.parametrize("data, code, loc", REQUESTS, ids=[f"{case[1]} {case[2]}" for case in REQUESTS])
def test_a_refused_request_has_one_located_detail_per_fault(data, code, loc):
    with pytest.raises(ServiceError) as raised:
        validated(StrategyRequest, data)
    assert raised.value.exit == Exit.USAGE
    assert [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_one_pass_reports_the_requests_errors_and_the_strategys_together():
    data = {"strategy": {"filters": [{"name": "a", "expr": ["GT", "amout", 1.0]}], "top_n": 0}}
    with pytest.raises(ServiceError) as raised:
        validated(StrategyRequest, data)
    got = [(d.code, d.loc, d.path) for d in raised.value.detail]
    assert got == [
        ("out_of_range", ["strategy", "top_n"], []),
        ("unknown_field", ["strategy", "filters", 0, "expr"], [1]),
    ]
    assert not raised.value.complete


def test_the_cores_errors_keep_their_codes_locations_and_expression_paths():
    spec = StrategySpec.model_validate(
        {"filters": [{"name": "score", "expr": [">", "close", 1]}], "factors": [{**FACTOR, "expr": ["MA", "close", 0]}]}
    )
    with pytest.raises(ServiceError) as raised:
        to_strategy(spec)
    assert [(d.code, d.loc, d.path) for d in raised.value.detail] == [
        ("reserved_name", ["strategy", "filters", 0, "name"], []),
        ("unknown_operator", ["strategy", "filters", 0, "expr"], []),
        ("out_of_range", ["strategy", "factors", 0, "expr"], [2]),
    ]


NOT_FOUND = [
    (lambda c: data_status(StatusRequest(), Context(c.home / "nothing")), "no_store", []),
    (lambda c: data_status(StatusRequest(data_version=9), c), "version_not_kept", ["data_version"]),
    (lambda c: data_bars(BarsRequest(codes=["600009.SH"]), c), "unknown_code", ["codes"]),
    (
        lambda c: data_bars(BarsRequest(codes=["600000.SH"], start="2024-01-06", end="2024-01-07"), c),
        "not_a_trading_day",
        [],
    ),
]


@pytest.mark.parametrize("call, code, loc", NOT_FOUND, ids=[case[1] for case in NOT_FOUND])
def test_not_found_names_what_is_missing(context, call, code, loc):
    with pytest.raises(ServiceError) as raised:
        call(context)
    assert raised.value.exit == Exit.NOT_FOUND
    assert [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]
