import json

import pytest

from wequant.data.store import Store
from wequant.rules.book import default_book
from wequant.service import CheckRequest, Context, Exit, ServiceError, StatusRequest, data_check, data_status


def test_the_seed_store(home, context):
    status = json.loads(data_status(StatusRequest(), context).model_dump_json())
    assert status == {
        "data_version": 1,
        "current_version": 1,
        "versions": [1],
        "created_at": Store(home).manifest()["created_at"],
        "last_trade_date": None,
        "limit_check_current": False,
        "tables": [
            {"name": "adj_factor", "files": 1, "rows": 3},
            {"name": "bars", "files": 1, "rows": 21},
            {"name": "calendar", "files": 1, "rows": 8},
            {"name": "data_issues", "files": 1, "rows": 7},
            {"name": "index_bars", "files": 1, "rows": 7},
            {"name": "securities", "files": 1, "rows": 4},
        ],
        "meta": {"source": "test"},
    }


def test_a_pinned_version(home_v2, context):
    latest = data_status(StatusRequest(), context)
    assert (latest.data_version, latest.current_version, latest.versions) == (2, 2, [1, 2])
    pinned = data_status(StatusRequest(data_version=1), context)
    assert (pinned.data_version, pinned.current_version, pinned.versions) == (1, 2, [1, 2])
    assert pinned.created_at == Store(home_v2).manifest(1)["created_at"]


def test_after_a_committed_check(limits_home):
    data_check(CheckRequest(commit=True), Context(limits_home))
    status = data_status(StatusRequest(), Context(limits_home))
    assert (status.data_version, status.versions, status.last_trade_date.isoformat()) == (2, [1, 2], "2024-01-08")
    assert status.limit_check_current
    rows = {t.name: t.rows for t in status.tables}
    assert rows == {"adj_factor": 1, "bars": 10, "calendar": 5, "data_issues": 2, "securities": 3}
    assert set(status.meta) == {"source", "release_tag", "limit_check"}
    assert (status.meta["source"], status.meta["release_tag"]) == ("test", "t1")
    block = status.meta["limit_check"]
    assert block["rules"] == default_book().digest and block["format"] == 1
    assert isinstance(block["inputs"], str) and isinstance(block["code_version"], str)


def test_no_store_or_no_such_version(home, tmp_path):
    with pytest.raises(ServiceError, match="no store at") as raised:
        data_status(StatusRequest(), Context(tmp_path / "empty"))
    assert raised.value.exit == Exit.NOT_FOUND
    with pytest.raises(ServiceError, match=r"store version 9 is not kept; kept: \[1\]") as raised:
        data_status(StatusRequest(data_version=9), Context(home))
    assert raised.value.exit == Exit.NOT_FOUND
