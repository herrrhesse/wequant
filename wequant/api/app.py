"""The HTTP API: one route per service operation, no logic of its own. A route takes the operation's request model as
its JSON body and answers with the bytes `wequant <command> --json` prints, or the ErrorBody of its failure. Beside
them, GET /api/jobs/{id}/events streams a background job's JobInfo as server-sent events."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import get_type_hints

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from wequant.service import Context, ErrorBody, ErrorDetail, Exit, JobRef, SchemaRequest, ServiceError, schema
from wequant.service.base import error_body
from wequant.service.contract import OPERATIONS, route
from wequant.service.details import error_details, usage, validated
from wequant.service.jobs import KINDS, jobs_show
from wequant.service.queue import FINAL, Jobs
from wequant.version import code_version

log = logging.getLogger("wequant")
STATUS = {  # HTTP status of each failure; a check that finds problems is a 200 with ok false
    Exit.USAGE: 422,
    Exit.NOT_FOUND: 404,
    Exit.UNREACHABLE: 503,
    Exit.LOCKED: 409,
    Exit.INTERNAL: 500,
    Exit.INTERRUPTED: 499,  # the client went away; nobody reads it
}
POLL = 0.25  # seconds between checks whether the client is still connected
TICK = 0.05  # seconds between checks whether a followed job changed
KEEPALIVE = 15.0  # seconds of quiet after which a job's event stream sends a comment
STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"  # the repository's frontend build
UNBUILT = """<!doctype html><meta charset="utf-8"><title>WeQuant</title>
<h1>前端未构建 · The frontend is not built</h1>
<p>在 frontend/ 运行 <code>npm ci && npm run build</code>，然后重启 <code>wequant serve</code>。接口照常工作：POST /api/&lt;操作名&gt;。</p>
<p>In frontend/, run <code>npm ci && npm run build</code>, then restart <code>wequant serve</code>. The API works: POST
/api/&lt;operation&gt;.</p>
<p>Looked for: {where}</p>
"""


def static_dir(given: Path | None = None) -> Path:
    """The frontend build to serve: given, else $WEQUANT_STATIC, else the repository's frontend/dist."""
    return Path(given or os.environ.get("WEQUANT_STATIC") or STATIC)


def create_app(context: Context, operations: Iterable[Callable] = OPERATIONS, static: Path | None = None) -> FastAPI:
    """The app serving operations (default: all of them) with context, which a resident process keeps between calls,
    and the frontend build at / (static_dir(static)), or a page saying how to build it. Without a job queue in context
    the app opens one over the data root; shutting the app down stops it."""
    if context.jobs is None:
        jobs = Jobs(context.store().home, KINDS, context.cache, clock=context.clock)
        context = dataclasses.replace(context, jobs=jobs)
    app = FastAPI(  # no Swagger or ReDoc page: they load scripts from a CDN and are blank offline
        title="WeQuant",
        version=code_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=_stopping(context.jobs),
    )
    for operation in operations:
        method, path = route(operation.__name__)
        hints = get_type_hints(operation)
        app.add_api_route(
            path,
            _endpoint(operation, hints["request"], context),
            methods=[method],
            name=operation.__name__,
            summary=(operation.__doc__ or "").split("\n")[0],
            openapi_extra=_documented(hints["request"], hints["return"]),
        )
    app.add_api_route("/api/jobs/{id}/events", _events(context), methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/{rest:path}", _no_route, methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)  # fmt: skip
    app.add_api_route("/{path:path}", _frontend(static_dir(static)), methods=["GET"], include_in_schema=False)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.openapi = lambda: _openapi(app)
    return app


def _frontend(root: Path) -> Callable:
    """GET of a file of the build; any other path its index.html (the app routes in the browser)."""
    root = root.resolve()
    index = root / "index.html"

    async def frontend(path: str) -> Response:
        if not index.is_file():
            return HTMLResponse(UNBUILT.format(where=root))
        found = (root / path).resolve()
        inside = found.is_relative_to(root) and found.is_file()
        return FileResponse(found if inside else index)

    return frontend


def _stopping(jobs: Jobs) -> Callable:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await anyio.to_thread.run_sync(jobs.close)

    return lifespan


def _events(context: Context) -> Callable:
    """GET of a job's events: its JobInfo now and after each change, until it has finished."""

    async def events(id: str) -> Response:
        try:
            ref = validated(JobRef, {"id": id})
            jobs_show(ref, context)
        except ServiceError as error:
            return _failure(error_body(error))
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(_follow(ref, context), media_type="text/event-stream", headers=headers)

    return events


async def _follow(ref: JobRef, context: Context) -> AsyncIterator[str]:
    """Server-sent events of a job: one "job" event per JobInfo that differs from the last, a comment when quiet."""
    seen, last, quiet = None, None, 0.0
    while True:
        if context.jobs.changes() != seen:
            seen = context.jobs.changes()
            try:
                info = jobs_show(ref, context)
            except ServiceError:  # dropped from the kept jobs
                return
            if (data := info.model_dump_json()) != last:
                last, quiet = data, 0.0
                yield f"event: job\ndata: {data}\n\n"
            if info.state in FINAL:
                return
        if quiet >= KEEPALIVE:
            quiet = 0.0
            yield ": keep-alive\n\n"
        await asyncio.sleep(TICK)
        quiet += TICK


async def _no_route(rest: str) -> Response:
    message = f"no operation at /api/{rest}; operations are POST /api/<operation>, listed by wequant schema"
    return _failure(error_body(ServiceError(Exit.NOT_FOUND, message, [ErrorDetail(code="no_route", message=message)])))


def _endpoint(operation: Callable, model: type, context: Context) -> Callable:
    async def endpoint(request: Request) -> Response:
        try:
            parsed = validated(model, await _body(request))
        except ServiceError as error:
            return _failure(error_body(error))
        gone = threading.Event()  # the client disconnected: a long run stops and leaves no archive
        watcher = asyncio.create_task(_watch(request, gone))
        try:
            call = dataclasses.replace(context, cancel=gone.is_set)
            return await anyio.to_thread.run_sync(_respond, operation, parsed, call)
        finally:
            watcher.cancel()

    return endpoint


def _respond(operation: Callable, request: object, context: Context) -> Response:
    """The result's JSON, or the failure's ErrorBody with its status; in a worker thread, so others are served."""
    try:
        result = operation(request, context)
    except ServiceError as error:
        log.error("%s: %s", operation.__name__, error)
        return _failure(error_body(error))
    except Exception as error:
        log.exception("internal error in %s", operation.__name__)
        message = f"{type(error).__name__}: {error}"
        return _failure(ErrorBody(exit=Exit.INTERNAL, name=Exit.INTERNAL.name, message=message))
    return Response(result.model_dump_json(), media_type="application/json")


async def _body(request: Request) -> dict:
    """The JSON object of the body, {} when it is empty; USAGE when it is not a JSON object."""
    raw = await request.body()
    try:
        found = json.loads(raw) if raw.strip() else {}
    except ValueError as error:
        raise usage([ErrorDetail(code="not_json", message=f"the body is not JSON: {error}")]) from None
    if not isinstance(found, dict):
        raise usage([ErrorDetail(code="wrong_type", message="the body is a JSON object of the request's fields")])
    return found


async def _watch(request: Request, gone: threading.Event) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(POLL)
    gone.set()


async def _invalid(request: Request, error: RequestValidationError) -> Response:
    """A validation error of the framework's own, located without its body/query prefix, as an ErrorBody."""
    errors = [e | {"loc": tuple(e["loc"][1:])} for e in error.errors()]
    return _failure(error_body(usage(error_details(errors))))


def _failure(body: ErrorBody) -> Response:
    return Response(body.model_dump_json(), status_code=STATUS.get(body.exit, 500), media_type="application/json")


def _documented(request: type, result: type) -> dict:
    """OpenAPI of a route: its request and result models and ErrorBody, from the service schema's components."""

    def ref(model: type) -> dict:
        return {"application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}

    return {
        "requestBody": {"required": False, "content": ref(request)},
        "responses": {"200": {"description": "the result", "content": ref(result)}},
    } | {"x-errors": {str(status): exit.name for exit, status in STATUS.items()}}


def _openapi(app: FastAPI) -> dict:
    """The generated document, its components taken from the service schema (the snapshot's source)."""
    if app.openapi_schema is None:
        document = get_openapi(title=app.title, version=app.version, routes=app.routes)
        models = json.loads(schema(SchemaRequest()).model_dump_json(by_alias=True))["$defs"]
        text = json.dumps(models).replace('"#/$defs/', '"#/components/schemas/')
        document.setdefault("components", {})["schemas"] = json.loads(text)
        app.openapi_schema = document
    return app.openapi_schema
