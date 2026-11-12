import json

from tests.service.test_cli import CASES
from wequant import service
from wequant.data.store import Store
from wequant.service import Context, StatusRequest, data_status


def test_the_seed_store(home, context):
    """The wire format of data status: versions with names and reasons, the manifest's time and meta, each table's
    files and rows."""
    status = json.loads(data_status(StatusRequest(), context).model_dump_json())
    created = Store(home).manifest()["created_at"]
    name = f"{created[:10].replace('-', '')}.1"  # no last trade date: the creation day, first version of it
    assert status == {
        "data_version": 1,
        "data_version_name": name,
        "current_version": 1,
        "version_in_use": 1,
        "using_old_version": False,
        "next_trading_day": None,  # no BaoStock calendar in the raw layer
        "update_due": None,
        "versions": [1],
        "version_list": [
            {
                "id": 1,
                "name": name,
                "reason": "未注明原因",
                "reason_code": "unspecified",
                "reason_params": {},
                "last_trade_date": None,
                "created_at": created,
                "pinned": False,
            }
        ],
        "created_at": created,
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
    """data_version reads that version; current_version still names the newest; version_list comes newest first."""
    latest = data_status(StatusRequest(), context)
    assert (latest.data_version, latest.current_version, [v.id for v in latest.version_list]) == (2, 2, [2, 1])
    pinned = data_status(StatusRequest(data_version=1), context)
    assert (pinned.data_version, pinned.current_version, pinned.data_version_name) == (
        1,
        2,
        latest.version_list[1].name,
    )
    assert pinned.created_at == Store(home_v2).manifest(1)["created_at"]


def test_every_result_with_a_data_version_names_it_as_data_status_does(cache, request):
    """F-29: a result that reports the version it read also gives that version's display name."""
    for _, name, request_, root in CASES:
        home = request.getfixturevalue(root)
        result = getattr(service, name)(request_, Context(home, cache))
        if "data_version" in type(result).model_fields:
            names = {v.id: v.name for v in data_status(StatusRequest(), Context(home)).version_list}
            assert result.data_version_name == names[result.data_version], name
