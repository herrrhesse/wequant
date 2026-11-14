"""wequant serve and the data root (round 3, F-30): switching without a restart, a copy as a background job, an empty
data root that still answers, and the folder reads byte for byte as the command prints them."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_panel import seed
from wequant import service
from wequant.api import create_app
from wequant.service import Context, JobInfo
from wequant.service.contract import OPERATIONS, route

COVERED = ("fs_places", "fs_list", "fs_mkdir", "data_use_home", "data_relocate", "setup_status", "setup_save")


def call(client, name: str, fields: dict | None = None):
    method, path = route(name)
    return client.request(method, path, content=json.dumps(fields or {}))


def test_a_switch_to_another_data_root_takes_effect_without_a_restart(market_home, tmp_path, cache):
    other = seed(tmp_path / "other").home
    with TestClient(create_app(Context.resident(market_home, cache))) as client:
        before = call(client, "data_status").json()
        assert call(client, "data_use_home", {"path": str(other)}).status_code == 200
        after = call(client, "data_status").json()
    assert (before["home"], after["home"]) == (str(market_home), str(other))
    assert after["tables"] != before["tables"]


def test_a_copy_job_switches_the_server_to_the_copy(market_home, tmp_path, cache):
    target = tmp_path / "copy"
    order = {"kind": "data_relocate", "request": {"to": str(target), "mode": "copy"}}
    with TestClient(create_app(Context.resident(market_home, cache))) as client:
        job = JobInfo.model_validate_json(call(client, "jobs_start", order).text)
        for _ in range(400):
            shown = JobInfo.model_validate_json(call(client, "jobs_show", {"id": job.id}).text)
            if shown.state not in ("queued", "running"):
                break
            time.sleep(0.05)
        status = call(client, "data_status").json()
        listed = [row["id"] for row in call(client, "jobs_list").json()["jobs"]]
    assert (shown.state, status["home"], job.id in listed) == ("succeeded", str(target), True)
    assert (market_home / "manifest.json").exists()  # a copy leaves the old folder


def test_an_empty_data_root_starts_and_no_operation_answers_500(tmp_path, cache, monkeypatch):
    """Every operation with an empty body on a folder that does not exist: a clear refusal, never a 500."""
    monkeypatch.setattr("wequant.data.sources.baostock.import_baostock", lambda: pytest.fail("no BaoStock here"))
    with TestClient(create_app(Context.resident(tmp_path / "missing", cache))) as client:
        answers = {op.__name__: call(client, op.__name__).status_code for op in OPERATIONS}
        status = call(client, "setup_status").json()
    assert [name for name, code in answers.items() if code >= 500] == []
    assert status["needs_setup"] and answers["data_status"] == 404


READS = [  # the same request on one folder: the command's --json and the route's body
    (["fs", "places"], "fs_places", {}),
    (["fs", "list", "{dir}"], "fs_list", {"path": "{dir}"}),
    (["setup", "status"], "setup_status", {}),
]


@pytest.mark.parametrize("argv, name, fields", READS, ids=[case[1] for case in READS])
def test_a_folder_read_returns_the_bytes_the_cli_prints(cli, tmp_path, cache, argv, name, fields):
    home = seed(tmp_path / "home").home
    fill = lambda value: value.format(dir=tmp_path) if isinstance(value, str) else value  # noqa: E731
    _, out, _ = cli(*map(fill, argv), "--json", "--home", home, "--cache", cache)
    with TestClient(create_app(Context.resident(home, cache))) as client:
        response = call(client, name, {key: fill(value) for key, value in fields.items()})
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


def test_a_new_folder_and_the_setup_s_progress_over_http(tmp_path, cache):
    with TestClient(create_app(Context.resident(tmp_path / "home", cache))) as client:
        made = call(client, "fs_mkdir", {"path": str(tmp_path), "name": "数据 2"}).json()
        saved = call(client, "setup_save", {"step": "folder", "home": made["path"]}).json()
    assert (tmp_path / "数据 2").is_dir() and saved["setup"]["home"] == str(tmp_path / "数据 2")
    assert service.setup_status(service.SetupStatusRequest(), Context(tmp_path / "home")).setup.step == "folder"
