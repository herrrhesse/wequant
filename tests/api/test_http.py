"""wequant serve's HTTP API: routes from the operation list, bodies byte for byte the CLI's --json, ErrorBody with the
F-12 status codes. Synthetic homes only."""

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import get_type_hints

import pytest
from fastapi.testclient import TestClient

from tests.frames import table_frame
from tests.service.conftest import MOM
from tests.service.test_cli import CASES, RUNS
from tests.test_merge_resolutions import EARLY
from tests.test_verifications import RECORD
from tests.update_fake import D4, Market, rows, seed_store
from wequant import service
from wequant.api import create_app
from wequant.cli.commands import DEFAULT_PORT
from wequant.data.merge.resolutions import add, default_path
from wequant.data.store import Store, exclusive
from wequant.service import Context, ErrorBody, Exit, JobInfo, JobRef, ServiceError
from wequant.service.contract import OPERATIONS
from wequant.service.jobs import KINDS
from wequant.service.queue import Jobs

PROPOSED = EARLY | {"status": "proposed", "approval": "", "category": "B"}
WRITES = [  # (argv, operation, request fields, home): each runs on its own copy of the home, CLI and HTTP alike
    (["strategy", "save", "{dir}/mom.json", "--id", "t"], "strategy_save", {"strategy": MOM, "id": "t"}, "library"),
    (["strategy", "archive", "s"], "strategy_archive", {"id": "s"}, "library"),
    (["data", "verify", "submit", "{dir}/records.json"], "data_verify_submit", {"records": [RECORD]}, "library"),
    (["data", "verify", "list"], "data_verify_list", {}, "verified"),
    (["data", "resolve", "list"], "data_resolve_list", {}, "resolutions"),
    (
        ["data", "resolve", "approve", "--category", "B", "--note", "n"],
        "data_resolve_approve",
        {"category": "B", "note": "n"},
        "resolutions",
    ),  # fmt: skip
    (
        ["data", "resolve", "reject", "--category", "B", "--note", "n"],
        "data_resolve_reject",
        {"category": "B", "note": "n"},
        "resolutions",
    ),  # fmt: skip
    (["data", "names", "--commit"], "data_names", {"commit": True}, "named"),
]


def post(home, cache, name: str, fields: dict):
    """The HTTP response of operation name on fields, from an app over home."""
    method, path = service.contract.route(name)
    client = TestClient(create_app(Context.resident(home, cache)))
    return client.request(method, path, content=json.dumps(fields))


def test_every_operation_of_the_snapshot_has_its_route_and_no_route_is_extra(home):
    """Adding an operation needs no change in wequant.api: the routes are the snapshot's (method, path) pairs."""
    listed = {(o.method, o.path) for o in service.schema(service.SchemaRequest()).operations}
    app = create_app(Context(home))
    documented = [r for r in app.routes if getattr(r, "include_in_schema", False)]  # not the docs, the catch-alls
    routes = {(m, r.path) for r in documented for m in r.methods}
    assert routes == listed and len(listed) == len(OPERATIONS)


def test_the_contract_cases_cover_every_operation():
    covered = {name for _, name, *_ in CASES + RUNS + WRITES + STORAGE + POOLS} | set(JOBS) | {"data_update"}
    assert covered == {operation.__name__ for operation in OPERATIONS}


STORAGE = [  # on copies of a market with one archived run {id}; {dir} holds its exports as in.*
    (["settings", "show"], "settings_show", {}),
    (["settings", "set", "--keep-versions", "3"], "settings_update", {"keep_versions": 3}),
    (["storage", "usage"], "storage_usage", {}),
    (["storage", "cleanup", "--dry-run"], "storage_cleanup", {"dry_run": True}),
    (["data", "use", "1"], "data_use_version", {"version": 1}),
    (["data", "pin", "1"], "data_pin_version", {"version": 1}),
    (["data", "export", "--path", "{dir}/v.wqver"], "data_export", {"path": "{dir}/v.wqver"}),
    (["data", "import", "{dir}/in.wqver"], "data_import", {"path": "{dir}/in.wqver"}),
    (["runs", "pin", "{id}"], "runs_pin", {"id": "{id}"}),
    (["runs", "delete", "{id}"], "runs_delete", {"id": "{id}"}),
    (["runs", "export", "{id}", "--path", "{dir}/r.wqrun"], "runs_export", {"id": "{id}", "path": "{dir}/r.wqrun"}),
    (["runs", "import", "{dir}/in.wqrun"], "runs_import", {"path": "{dir}/in.wqrun"}),
    (["strategy", "export", "m", "--path", "{dir}/s.wqs"], "strategy_export", {"id": "m", "path": "{dir}/s.wqs"}),
    (["strategy", "import", "{dir}/in.wqstrategy"], "strategy_import", {"path": "{dir}/in.wqstrategy"}),
]


@pytest.mark.parametrize("argv, name, fields", STORAGE, ids=[case[1] for case in STORAGE])
def test_a_storage_operation_returns_the_bytes_the_cli_prints(cli, market_home, cache, tmp_path, argv, name, fields):
    origin = Context(market_home)
    run_id = service.backtest(service.BacktestRequest(saved={"id": "m"}), origin).run_id
    service.data_export(service.ExportVersionRequest(path=str(tmp_path / "in.wqver")), origin)
    service.runs_export(service.ExportRunRequest(id=run_id, path=str(tmp_path / "in.wqrun")), origin)
    service.strategy_export(service.ExportStrategyRequest(id="m", path=str(tmp_path / "in.wqstrategy")), origin)
    by_cli, by_http = (shutil.copytree(market_home, tmp_path / side) for side in ("cli", "http"))
    fill = lambda value: value.format(dir=tmp_path, id=run_id) if isinstance(value, str) else value  # noqa: E731
    _, out, _ = cli(*map(fill, argv), "--json", "--home", by_cli, "--cache", cache)
    response = post(by_http, cache, name, {key: fill(value) for key, value in fields.items()})
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


CSV = "代码,名称\n600004.SH,\n000001.SZ,\n"
POOLS = [  # on copies of a market with stock pool {pool} and CSI 300 members; {dir}/in.csv holds CSV
    (["pools", "list"], "pools_list", {}),
    (["pools", "show", "{pool}"], "pools_show", {"id": "{pool}"}),
    (
        ["pools", "save", "600004.SH", "--name", "b", "--id", "{pool}"],
        "pools_save",
        {"id": "{pool}", "name": "b", "codes": ["600004.SH"]},
    ),  # fmt: skip
    (["pools", "delete", "{pool}"], "pools_delete", {"id": "{pool}"}),
    (["pools", "parse", "600004 x"], "pools_parse", {"text": "600004 x"}),
    (["pools", "from-index", "000300.SH"], "pools_from_index", {"index": "000300.SH"}),
    (["pools", "export", "{pool}"], "pools_export", {"id": "{pool}"}),
    (["pools", "import", "{dir}/in.csv", "--name", "c"], "pools_import", {"name": "c", "text": CSV}),
]


@pytest.mark.parametrize("argv, name, fields", POOLS, ids=[case[1] for case in POOLS])
def test_a_pool_operation_returns_the_bytes_the_cli_prints(
    cli, market_home, cache, tmp_path, monkeypatch, argv, name, fields
):
    """New pools get the same id and every write the same time on both sides."""
    members = [{"index_code": "399300.SZ", "code": "000001.SZ", "start_date": "2024-01-02", "end_date": "2024-01-11"}]
    Store(market_home).commit({}, replace={"index_members": table_frame("index_members", members)})
    pool = service.pools_save(service.PoolSaveRequest(name="a", codes=["000001.SZ"]), Context(market_home)).id
    (tmp_path / "in.csv").write_text(CSV, encoding="utf-8")
    monkeypatch.setattr("wequant.pools.book.utc_now", lambda: "2026-11-14T00:00:00+00:00")
    monkeypatch.setattr("wequant.pools.book.secrets.token_hex", lambda n: "0" * 2 * n)
    by_cli, by_http = (shutil.copytree(market_home, tmp_path / side) for side in ("cli", "http"))
    fill = lambda value: value.format(dir=tmp_path, pool=pool) if isinstance(value, str) else value  # noqa: E731
    _, out, _ = cli(*map(fill, argv), "--json", "--home", by_cli, "--cache", cache)
    response = post(by_http, cache, name, {key: fill(value) for key, value in fields.items()})
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


def test_the_update_returns_the_bytes_the_cli_prints(cli, tmp_path, cache, monkeypatch):
    """data update against a fake BaoStock, on two copies of one seed store."""
    market = Market(days={D4: rows(D4)})
    monkeypatch.setattr("wequant.data.sources.baostock.import_baostock", lambda: market)
    by_cli, by_http = (seed_store(tmp_path / side).home for side in ("cli", "http"))
    _, out, _ = cli("data", "update", "--json", "--home", by_cli)
    response = post(by_http, cache, "data_update", {})
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


JOBS = ("jobs_start", "jobs_list", "jobs_show", "jobs_cancel", "jobs_result")  # HTTP only: the queue lives in serve
BACKTEST_JOB = json.dumps({"kind": "backtest", "request": {"saved": {"id": "m"}}})


def events(client, id: int) -> list[JobInfo]:
    """The JobInfo of each event of job id's stream, which ends once the job has finished."""
    with client.stream("GET", f"/api/jobs/{id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in response.iter_lines() if line.startswith("data: ")]
    return [JobInfo.model_validate_json(line.removeprefix("data: ")) for line in lines]


def test_the_job_routes_answer_with_their_operations_bytes(market_home, cache):
    """Once a job has finished: show, list, cancel and result answer with their operations' JSON; the result is the
    bytes POST /api/backtest answers with."""
    context = Context(market_home, cache, jobs=Jobs(market_home, KINDS, cache))
    with TestClient(create_app(context)) as client:
        id = JobInfo.model_validate_json(client.post("/api/jobs_start", content=BACKTEST_JOB).text).id
        events(client, id)
        bodies = {name: client.post(f"/api/{name}", content=json.dumps({"id": id})).text for name in JOBS[2:]}
        bodies["jobs_list"] = client.post("/api/jobs_list").text
        direct = client.post("/api/backtest", content=json.dumps({"saved": {"id": "m"}})).text
    ref, listed = JobRef(id=id), service.jobs_list(service.JobsListRequest(), context)
    expected = {name: getattr(service, name)(ref, context).model_dump_json() for name in JOBS[2:]}
    assert bodies == expected | {"jobs_list": listed.model_dump_json()} and bodies["jobs_result"] == direct


def test_a_job_s_events_carry_each_change_until_it_has_finished(market_home, cache):
    with TestClient(create_app(Context(market_home, cache))) as client:
        id = JobInfo.model_validate_json(client.post("/api/jobs_start", content=BACKTEST_JOB).text).id
        infos = events(client, id)
    fractions = [info.fraction for info in infos]
    assert infos[-1].state == "succeeded" and fractions == sorted(fractions) and len({*map(str, infos)}) == len(infos)


def test_an_export_downloads_and_an_uploaded_file_imports(market_home, cache):
    run_id = service.backtest(service.BacktestRequest(saved={"id": "m"}), Context(market_home)).run_id
    written = service.runs_export(service.ExportRunRequest(id=run_id), Context(market_home))  # into exports/
    with TestClient(create_app(Context(market_home, cache))) as client:
        data = client.get(f"/api/exports/{run_id}.wqrun").content
        path = client.put(f"/api/imports/{run_id}.wqrun", content=data).json()["path"]
        imported = client.post("/api/runs_import", content=json.dumps({"path": path})).json()
    assert data == Path(written.path).read_bytes() and imported == {"id": run_id, "added": False}


def test_only_exports_download_and_only_export_files_upload(home):
    client = TestClient(create_app(Context(home)))
    missing, refused = client.get("/api/exports/none.wqrun"), client.put("/api/imports/notes.txt", content=b"x")
    codes = [ErrorBody.model_validate_json(r.text).detail[0].code for r in (missing, refused)]
    assert ([missing.status_code, refused.status_code], codes) == ([404, 422], ["no_file", "bad_file"])


def test_the_events_of_an_unknown_job_are_a_404_error_body(home):
    response = TestClient(create_app(Context(home))).get("/api/jobs/7/events")
    error = ErrorBody.model_validate_json(response.text)
    assert (response.status_code, error.detail[0].code) == (404, "unknown_job")


@pytest.mark.parametrize("argv, name, request_, root", CASES, ids=[case[1] for case in CASES])
def test_a_read_returns_the_bytes_the_cli_prints(cli, cache, request, argv, name, request_, root):
    """Same request, same bytes: the response body is the CLI's --json output without its newline; a check that
    finds problems is still a 200."""
    home = request.getfixturevalue(root)
    _, out, _ = cli(*argv, "--json", "--home", home, "--cache", cache)
    response = post(home, cache, name, request_.model_dump(mode="json", exclude_unset=True))
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


@pytest.mark.parametrize("argv, name, fields", RUNS, ids=[case[1] for case in RUNS])
def test_a_run_read_returns_the_bytes_the_cli_prints(cli, market_home, cache, argv, name, fields):
    run_id = service.backtest(service.BacktestRequest(saved={"id": "m"}), Context(market_home)).run_id
    _, out, _ = cli(*[a.format(id=run_id) for a in argv], "--json", "--home", market_home, "--cache", cache)
    fields = {k: v.format(id=run_id) if isinstance(v, str) else v for k, v in fields.items()}
    response = post(market_home, cache, name, fields)
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


@pytest.mark.parametrize("argv, name, fields, root", WRITES, ids=[case[1] for case in WRITES])
def test_a_write_returns_the_bytes_the_cli_prints(cli, library_home, named_home, cache, tmp_path, monkeypatch, argv, name, fields, root):  # fmt: skip
    monkeypatch.setattr("wequant.strategy.library.utc_now", lambda: "2026-11-11T00:00:00+00:00")
    origin = {"library": library_home, "verified": library_home, "resolutions": library_home, "named": named_home}[root]
    if root == "verified":
        service.data_verify_submit(service.VerifySubmitRequest(records=[RECORD]), Context(origin))
    if root == "resolutions":
        add(default_path(origin), [PROPOSED])
    (tmp_path / "mom.json").write_text(json.dumps(MOM), encoding="utf-8")
    (tmp_path / "records.json").write_text(json.dumps([RECORD]), encoding="utf-8")
    by_cli, by_http = (shutil.copytree(origin, tmp_path / side) for side in ("cli", "http"))
    _, out, _ = cli(*[a.format(dir=tmp_path) for a in argv], "--json", "--home", by_cli)
    response = post(by_http, cache, name, fields)
    assert (response.status_code, response.text) == (200, out.removesuffix("\n"))


def test_a_failure_returns_the_cli_s_error_body_with_its_status(cli, tmp_path, cache):
    """NOT_FOUND: the same ErrorBody bytes as the CLI, status 404."""
    _, out, _ = cli("data", "status", "--json", "--home", tmp_path / "none")
    response = post(tmp_path / "none", cache, "data_status", {})
    assert (response.status_code, response.text) == (404, out.removesuffix("\n"))


@pytest.mark.parametrize(
    "body, code, loc",
    [(b"{bad", "not_json", []), (b"[1]", "wrong_type", []), (b'{"codes": ["600000"]}', "wrong_type", ["codes", 0])],
    ids=["not json", "not an object", "invalid field"],
)
def test_an_invalid_body_is_a_422_error_body_located_like_pydantic(home, body, code, loc):
    response = TestClient(create_app(Context(home))).post("/api/data_bars", content=body)
    error = ErrorBody.model_validate_json(response.text)
    assert (response.status_code, error.exit, error.detail[0].code, error.detail[0].loc) == (422, 2, code, loc)


def test_the_framework_s_own_validation_errors_are_error_bodies_too(home):
    def extra(n: int) -> int:  # a route the framework validates itself: n is a required query parameter
        return n

    app = create_app(Context(home))
    app.add_api_route("/api/extra", extra, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the catch-all routes
    response = TestClient(app).get("/api/extra")
    error = ErrorBody.model_validate_json(response.text)
    assert (response.status_code, error.name, error.detail[0].loc) == (422, "USAGE", ["n"])


def test_a_held_lock_is_a_409(library_home, cache):
    """Writes take the data root's locks as before: with strategy s's lock held, saving to s answers LOCKED."""
    with exclusive(library_home / "strategies" / "s" / ".lock"):
        response = post(library_home, cache, "strategy_save", {"strategy": MOM, "id": "s"})
    assert (response.status_code, ErrorBody.model_validate_json(response.text).exit) == (409, Exit.LOCKED)


def raising(error: BaseException):
    """A data_status operation that raises error."""

    def data_status(request: service.StatusRequest, context: Context) -> service.DataStatus:
        raise error

    return data_status


STATUSES = {"USAGE": 422, "NOT_FOUND": 404, "UNREACHABLE": 503, "LOCKED": 409, "INTERNAL": 500, "INTERRUPTED": 499}


@pytest.mark.parametrize("name, status", STATUSES.items(), ids=STATUSES)
def test_a_service_error_answers_with_its_status_and_its_error_body(home, name, status):
    """F-12's mapping (a vanished client's INTERRUPTED never reaches anyone, 499)."""
    error = ServiceError(Exit[name], "why")
    response = TestClient(create_app(Context(home), [raising(error)])).post("/api/data_status")
    assert (response.status_code, ErrorBody.model_validate_json(response.text).name) == (status, name)


def test_an_unexpected_exception_is_a_500_internal_error_body(home):
    response = TestClient(create_app(Context(home), [raising(ZeroDivisionError("bug"))])).post("/api/data_status")
    body = ErrorBody(exit=Exit.INTERNAL, name="INTERNAL", message="ZeroDivisionError: bug")
    assert (response.status_code, response.text) == (500, body.model_dump_json())


def test_a_client_that_disconnects_cancels_its_run(home):
    """A long run polls context.cancel; it turns true once the client is gone, so the run stops (and archives
    nothing)."""
    seen = []

    def backtest(request: service.BacktestRequest, context: Context) -> service.BacktestResult:
        for _ in range(400):
            if context.cancel():
                seen.append(True)
                raise ServiceError(Exit.INTERRUPTED, "cancelled")
            time.sleep(0.01)
        raise AssertionError("never cancelled")

    app = create_app(Context(home), [backtest])
    messages = [{"type": "http.request", "body": json.dumps({"strategy": MOM}).encode(), "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        pass

    scope = {"type": "http", "method": "POST", "path": "/api/backtest", "headers": [], "query_string": b""}
    asyncio.run(app(scope | {"asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http"}, receive, send))
    assert seen == [True]


def test_the_request_and_result_of_each_route_are_its_operation_s(home):
    """OpenAPI documents each route with the operation's own models, taken from the service schema."""
    document = TestClient(create_app(Context(home))).get("/api/openapi.json").json()
    operation = document["paths"]["/api/data_bars"]["post"]
    hints = get_type_hints(service.data_bars)
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"] == f"#/components/schemas/{hints['request'].__name__}"
    assert hints["request"].__name__ in document["components"]["schemas"]


def test_serve_runs_one_resident_process_and_warns_off_the_loopback(cli, monkeypatch, tmp_path):
    """Wiring of wequant serve: loopback and the default port unless told otherwise, one resident context whose
    expression cache holds --cache-limit GB; another address is served with a warning on stderr."""
    started = []
    monkeypatch.setattr("wequant.api.server.run_server", lambda *args: started.append(args))
    code, _, err = cli("serve", "--home", tmp_path, "--cache-limit", "1")
    context, host, port, _ = started[-1]
    assert (code, err, host, port) == (0, "", "127.0.0.1", DEFAULT_PORT)
    assert (context.home, context.expr_cache.max_bytes) == (tmp_path, 1 << 30)
    code, _, err = cli("serve", "--host", "0.0.0.0", "--port", "9000")
    assert started[-1][1:3] == ("0.0.0.0", 9000) and "WARNING" in err


def test_the_frontend_build_is_served_at_the_root_and_the_api_stays_apart(home, tmp_path):
    """A file of the build is served as is, any other path gets index.html (the browser routes); /api paths never
    fall through to it, an unknown one is a 404 ErrorBody."""
    (tmp_path / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (tmp_path / "dist" / "assets" / "a.js").write_text("js", encoding="utf-8")
    client = TestClient(create_app(Context(home), static=tmp_path / "dist"))
    assert [client.get(p).text for p in ("/", "/assets/a.js", "/picks", "/../secret")] == ["<html>app</html>", "js", "<html>app</html>", "<html>app</html>"]  # fmt: skip
    missing = client.post("/api/no_such_operation")
    assert (missing.status_code, ErrorBody.model_validate_json(missing.text).detail[0].code) == (404, "no_route")


def test_without_a_build_the_root_says_how_to_build_it(home, tmp_path):
    response = TestClient(create_app(Context(home), static=tmp_path / "none")).get("/")
    assert response.status_code == 200 and "npm run build" in response.text


def test_the_openapi_document_is_served_without_a_page_that_needs_the_internet(home):
    client = TestClient(create_app(Context(home), static=home / "no-build"))
    assert client.get("/api/openapi.json").status_code == 200
    assert [client.get(path).status_code for path in ("/api/docs", "/api/redoc")] == [404, 404]
    assert client.get("/redoc").text == client.get("/").text  # the frontend's page, not FastAPI's ReDoc
