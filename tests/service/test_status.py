import json

from wequant.data.store import Store
from wequant.service import StatusRequest, data_status


def test_the_seed_store(home, context):
    """The wire format of data status: versions, the manifest's time and meta, each table's files and rows."""
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
    """data_version reads that version; current_version still names the newest."""
    latest = data_status(StatusRequest(), context)
    assert (latest.data_version, latest.current_version, latest.versions) == (2, 2, [1, 2])
    pinned = data_status(StatusRequest(data_version=1), context)
    assert (pinned.data_version, pinned.current_version, pinned.versions) == (1, 2, [1, 2])
    assert pinned.created_at == Store(home_v2).manifest(1)["created_at"]
