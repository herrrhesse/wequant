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
REQUESTS = [  # one per code: pydantic's types mapped, the fallback wrong_type, and the custom errors passed on
    ({"strategy": {"zz": 1}}, "unknown_key", ["strategy", "zz"]),
    ({"strategy": {"factors": [{"expr": "close"}]}}, "missing_key", ["strategy", "factors", 0, "name"]),
    ({"strategy": {"period": "year"}}, "not_a_choice", ["strategy", "period"]),
    ({"saved": {"id": "s", "n": 0}}, "out_of_range", ["saved", "n"]),  # no inline strategy: pydantic alone
    ({"strategy": {"top_n": "5"}}, "wrong_type", ["strategy", "top_n"]),
    ({}, "requires", []),
    (
        {"strategy": {"filters": [{"name": "g", "conditions": [{"name": "c", "expr": 5}]}]}},
        "not_a_form",
        ["strategy", "filters", 0, "conditions", 0, "expr"],
    ),
]


@pytest.mark.parametrize("data, code, loc", REQUESTS, ids=[f"{case[1]} {case[2]}" for case in REQUESTS])
def test_a_refused_request_has_one_located_detail_per_fault(data, code, loc):
    """Each pydantic error becomes a detail with a stable code and its loc, list indexes included; after a field error
    the rules relating fields have not run, so complete is False."""
    with pytest.raises(ServiceError) as raised:
        validated(StrategyRequest, data)
    assert raised.value.exit == Exit.USAGE and raised.value.complete == (loc == [])
    assert [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_one_pass_reports_the_requests_errors_and_the_strategys_together():
    """One failure reports every field error, the request's and the strategy core's; complete is False."""
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
    """NOT_FOUND (exit 3) with its stable code: no store, a version not kept (manifest), an unknown code, no trading day
    (open_panel)."""
    with pytest.raises(ServiceError) as raised:
        call(context)
    assert raised.value.exit == Exit.NOT_FOUND
    assert [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_a_bug_while_opening_the_panel_is_not_not_found(context, monkeypatch):
    """b6c9a93: only the panel's missing code or day is NOT_FOUND; any other error is a bug (the CLI exits 70)."""

    def broken(*args, **kwargs):
        raise IndexError("bug")

    monkeypatch.setattr("wequant.service.base.load_panel", broken)
    with pytest.raises(IndexError):
        data_bars(BarsRequest(codes=["600000.SH"]), context)
