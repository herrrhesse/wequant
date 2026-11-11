import datetime as dt

import polars as pl
import pytest

from wequant.data.issues import issue_rows
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
    """data_issues detail: one JSON object per row with sorted keys and ISO dates, as the seed import wrote them;
    null without detail columns."""
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
    assert issue_rows(ROWS, "bars", "bar_added", "row_added", [], "s")["detail"].to_list() == [None, None]


def test_nan_details_raise():
    """A detail is valid JSON: NaN raises instead of being written."""
    with pytest.raises(ValueError):
        issue_rows(ROWS.with_columns(b=pl.lit(float("nan"))), "bars", "close_unconfirmed", "flagged", ["b"], "s")


def test_issue_rows_checks_its_kind_against_issue_kinds():
    """Wiring of A-1: the shared writer refuses an unclassified kind."""
    with pytest.raises(ValueError, match="add it to ISSUE_KINDS"):
        issue_rows(ROWS, "bars", "k", "flagged", [], "s")
