"""wequant serve's HTTP API: routes from the operation list, bodies byte for byte the CLI's --json, ErrorBody with the
F-12 status codes. Synthetic homes only."""

import asyncio
import json
import shutil
import time
from typing import get_type_hints

import pytest
from fastapi.testclient import TestClient

from tests.service.conftest import MOM
from tests.service.test_cli import CASES, RUNS
from tests.test_merge_resolutions import EARLY
from tests.test_verifications import RECORD
from wequant import service
from wequant.api import create_app
from wequant.cli.commands import DEFAULT_PORT
from wequant.data.merge.resolutions import add, default_path
from wequant.data.store import exclusive
from wequant.service import Context, ErrorBody, Exit, ServiceError
from wequant.service.contract import OPERATIONS

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
    covered = {name for _, name, *_ in CASES + RUNS + WRITES}
    assert covered == {operation.__name__ for operation in OPERATIONS}


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
def test_a_write_returns_the_bytes_the_cli_prints(cli, library_home, cache, tmp_path, monkeypatch, argv, name, fields, root):  # fmt: skip
    monkeypatch.setattr("wequant.strategy.library.utc_now", lambda: "2026-11-11T00:00:00+00:00")
    origin = {"library": library_home, "verified": library_home, "resolutions": library_home}[root]
    if root == "verified":
        service.data_verify_submit(service.VerifySubmitRequest(records=[RECORD]), Context(origin))
    if root == "resolutions":
        add(default_path(origin), [PROPOSED])
    (tmp_path / "mom.json").write_text(json.dumps(MOM))
    (tmp_path / "records.json").write_text(json.dumps([RECORD]))
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
    (tmp_path / "dist" / "index.html").write_text("<html>app</html>")
    (tmp_path / "dist" / "assets" / "a.js").write_text("js")
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
