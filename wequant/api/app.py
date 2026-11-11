"""The HTTP API: one route per service operation, no logic of its own. A route takes the operation's request model as
its JSON body and answers with the bytes `wequant <command> --json` prints, or the ErrorBody of its failure."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
from collections.abc import Callable, Iterable
from typing import get_type_hints

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import Response

from wequant.service import Context, ErrorBody, ErrorDetail, Exit, SchemaRequest, ServiceError, schema
from wequant.service.base import error_body
from wequant.service.contract import OPERATIONS, route
from wequant.service.details import error_details, usage, validated
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


def create_app(context: Context, operations: Iterable[Callable] = OPERATIONS) -> FastAPI:
    """The app serving operations (default: all of them) with context, which a resident process keeps between calls."""
    app = FastAPI(title="WeQuant", version=code_version(), docs_url="/api/docs", openapi_url="/api/openapi.json")
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
    app.add_exception_handler(RequestValidationError, _invalid)
    app.openapi = lambda: _openapi(app)
    return app


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
