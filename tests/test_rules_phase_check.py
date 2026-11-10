import datetime as dt
import json

import polars as pl

from tests.frames import table_frame
from tests.test_rules_limits import inputs
from wequant.data.schema import TABLES
from wequant.rules.check import ACTION, KIND, limit_issues, void
from wequant.rules.limits import FREE, LIMITED, UNKNOWN, price_limits
from wequant.rules.phase import add_listing_day, list_dates

CALENDAR = pl.Series("date", [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10)])


def test_codes_present_from_their_exchanges_first_day_have_no_list_date():
    securities = table_frame(
        "securities",
        [
            {"code": "600000.SH", "exchange": "SH", "first_date": "2000-01-04"},
            {"code": "600001.SH", "exchange": "SH", "first_date": "2001-03-05"},
            {"code": "000001.SZ", "exchange": "SZ", "first_date": "2000-01-04"},
            {"code": "830799.BJ", "exchange": "BJ", "first_date": "2021-10-27"},
            {"code": "920001.BJ", "exchange": "BJ", "first_date": "2025-10-09"},
        ],
        board="main",
        last_date="2026-11-06",
        source="test",
    )
    out = dict(list_dates(securities).iter_rows())
    assert out == {
        "600000.SH": None,
        "600001.SH": dt.date(2001, 3, 5),
        "000001.SZ": None,
        "830799.BJ": None,
        "920001.BJ": dt.date(2025, 10, 9),
    }


def phase_frame() -> pl.DataFrame:
    rows = [
        ("600001.SH", "2024-01-03", "2024-01-03"),
        ("600001.SH", "2024-01-04", "2024-01-03"),
        ("600001.SH", "2024-01-09", "2024-01-03"),
        ("600000.SH", "2024-01-10", None),
        ("600000.SH", "2024-01-02", None),
    ]
    frame = pl.DataFrame(rows, schema=["code", "date", "list_date"], orient="row")
    return frame.with_columns(pl.col("date", "list_date").str.to_date())


def test_listing_day_counts_trading_days_suspended_or_not():
    out = add_listing_day(phase_frame(), CALENDAR)
    assert out["listing_day"].to_list() == [1, 2, 5, None, None]
    assert out.schema["listing_day"] == pl.Int32


def checked(rows: list[dict]) -> pl.DataFrame:
    """price_limits of main-board rows on 2024-01-03 plus the check's extra columns (high, low default to preclose)."""
    rows = [{"date": "2024-01-03", "board": "main", **row} for row in rows]
    frame = price_limits(inputs(rows))
    extra = pl.DataFrame(
        {
            "high": [r.get("high", r.get("preclose", 10000)) for r in rows],
            "low": [r.get("low", r.get("preclose", 10000)) for r in rows],
            "gap_days": [0] * len(rows),
            "ex_date": [False] * len(rows),
            "preclose_derived": [True] * len(rows),
        },
        schema_overrides={"high": pl.Int32, "low": pl.Int32, "gap_days": pl.Int32},
    )
    return frame.hstack(extra)


def test_a_breach_becomes_one_valid_issue_with_measured_values():
    frame = checked(
        [
            {"code": "600000.SH", "preclose": 10000, "high": 11010, "low": 9900},
            {"code": "600001.SH", "preclose": 10000, "high": 11000, "low": 9000},
            {"code": "600002.SH", "list_date": "2024-01-03", "listing_day": 1, "high": 20000, "low": 5000},
            {"code": "600003.SH", "high": 20000, "low": 5000},
        ]
    )
    assert frame["limit_state"].to_list() == [LIMITED, LIMITED, FREE, UNKNOWN]
    issues = limit_issues(frame, "limit_check@abc")
    TABLES["data_issues"].validate(issues)
    assert issues.select("table", "code", "date", "kind", "action", "source").rows() == [
        ("bars", "600000.SH", dt.date(2024, 1, 3), KIND, ACTION, "limit_check@abc")
    ]
    assert json.loads(issues["detail"][0]) == {
        "ex_date": False,
        "gap_days": 0,
        "high": 11010,
        "is_st": None,
        "limit_down": 9000,
        "limit_rule": "band-main-1996",
        "limit_up": 11000,
        "low": 9900,
        "preclose": 10000,
        "preclose_derived": True,
    }


def test_void_nulls_exactly_the_flagged_days():
    frame = checked([{"code": c, "preclose": 10000} for c in ("600000.SH", "600001.SH")])
    flagged = table_frame(
        "data_issues",
        [{"code": "600001.SH", "date": "2024-01-03"}, {"code": "600000.SH", "date": "2024-01-04"}],
        table="bars",
        kind=KIND,
        action=ACTION,
        source="s",
    )
    other = table_frame(
        "data_issues",
        [{"code": "600000.SH", "date": "2024-01-03"}],
        table="bars",
        kind="k",
        action="flagged",
        source="s",
    )
    out = void(frame, pl.concat([flagged, other]))
    assert out.columns == frame.columns
    assert out.select("code", "limit_up", "limit_down", "limit_state").rows() == [
        ("600000.SH", 11000, 9000, LIMITED),
        ("600001.SH", None, None, UNKNOWN),
    ]
