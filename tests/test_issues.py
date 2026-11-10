import datetime as dt
import json

import polars as pl
import pytest

from wequant.data.issues import encode, issue_rows
from wequant.data.schema import TABLES

ROWS = pl.DataFrame(
    {
        "code": ["600000.SH", "000001.SZ"],
        "date": [dt.date(2024, 1, 3), dt.date(2024, 1, 4)],
        "b": [1.5, 2.0],
        "a": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
    }
)


def test_detail_columns_become_one_json_object_per_row():
    issues = issue_rows(ROWS, "bars", "volume_mismatch", "value_replaced", ["b", "a"], "merge@1")
    TABLES["data_issues"].validate(issues)
    assert issues["detail"].to_list() == ['{"a": "2024-01-02", "b": 1.5}', '{"a": "2024-01-03", "b": 2.0}']
    assert issues.row(0, named=True) == {
        "table": "bars",
        "code": "600000.SH",
        "date": dt.date(2024, 1, 3),
        "kind": "volume_mismatch",
        "action": "value_replaced",
        "detail": '{"a": "2024-01-02", "b": 1.5}',
        "source": "merge@1",
    }


def test_encoding_matches_the_seed():
    detail = {"close": 7.21, "amount": 2479699.95}
    assert encode(detail) == '{"amount": 2479699.95, "close": 7.21}'
    assert encode(detail) == json.dumps(detail, sort_keys=True, ensure_ascii=False)


def test_nan_details_raise():
    with pytest.raises(ValueError):
        issue_rows(ROWS.with_columns(b=pl.lit(float("nan"))), "bars", "close_unconfirmed", "flagged", ["b"], "s")


def test_no_detail_columns_give_null_details():
    issues = issue_rows(ROWS, "bars", "bar_added", "row_added", [], "s")
    assert issues["detail"].null_count() == 2


def test_a_kind_outside_issue_kinds_is_refused():
    with pytest.raises(ValueError, match="add it to ISSUE_KINDS"):
        issue_rows(ROWS, "bars", "k", "flagged", [], "s")


def test_no_rows_give_an_empty_frame_with_the_schema():
    issues = issue_rows(ROWS.clear(), "bars", "close_unconfirmed", "flagged", ["a", "b"], "s")
    assert issues.height == 0 and dict(issues.schema) == TABLES["data_issues"].schema
