"""schema: one JSON Schema document of every operation's request and result model and of ErrorBody."""

from __future__ import annotations

from typing import get_type_hints

from pydantic import ConfigDict, Field, JsonValue
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from wequant.service import runs, strategies
from wequant.service.backtest import backtest
from wequant.service.bars import data_bars
from wequant.service.base import Context, ErrorBody, Model
from wequant.service.calendar import data_calendar
from wequant.service.check import data_check
from wequant.service.delistings import data_delistings
from wequant.service.explain import explain
from wequant.service.expr import expr_check, expr_eval
from wequant.service.index_check import data_index_check
from wequant.service.issues import data_issues
from wequant.service.profile import ProfileReport
from wequant.service.resolutions import data_resolve_approve, data_resolve_list, data_resolve_reject
from wequant.service.screen import screen
from wequant.service.securities import data_securities
from wequant.service.status import data_status
from wequant.service.verify import data_verify_list, data_verify_submit

DIALECT = "https://json-schema.org/draft/2020-12/schema"
REGENERATE = "uv run wequant schema --json > frontend/contract/service-models.schema.json"


class SchemaRequest(Model):
    """The JSON Schema of every service model."""


class OperationModels(Model):
    """One operation, the names of its request and result models, and its HTTP route."""

    operation: str
    """The function in wequant.service, e.g. data_bars for wequant data bars."""
    request: str
    result: str
    available: bool
    """False: its models are fixed and its command is still to come."""
    method: str
    """HTTP method of its route under wequant serve: always POST, the request model as the JSON body."""
    path: str
    """Its route: /api/ and the operation's name, e.g. /api/data_bars."""


def route(operation: str) -> tuple[str, str]:
    """(method, path) of an operation's HTTP route: POST /api/<operation>, the one rule for every operation."""
    return "POST", f"/api/{operation}"


class ServiceSchema(Model):
    """A JSON Schema document: $defs holds every service model by name, in alphabetical order."""

    model_config = ConfigDict(serialize_by_alias=True)

    dialect: str = Field(DIALECT, alias="$schema")
    title: str = "WeQuant service models"
    description: str = (
        f"Each operation's request and result, and ErrorBody, the body of a failed one. From: {REGENERATE}"
    )
    operations: list[OperationModels]
    """Each operation's request and result model, in the order of the commands."""
    defs: dict[str, JsonValue] = Field(alias="$defs")
    """Each model's JSON Schema by name."""


class Untitled(GenerateJsonSchema):
    """Field titles only repeat the field names, and TypeScript generators turn each into a numbered type alias."""

    def field_title_should_be_set(self, schema) -> bool:
        return False


def schema(request: SchemaRequest, context: Context = Context()) -> ServiceSchema:
    """Requests as the operations accept them, results and ErrorBody as they print them; the same for the same code."""
    available = [(f.__name__, get_type_hints(f)["request"], get_type_hints(f)["return"], True) for f in OPERATIONS]
    rows = available + [(*planned, False) for planned in PLANNED]
    models = [(request, "validation") for _, request, _, _ in rows] + [
        (result, "serialization") for _, _, result, _ in rows
    ]
    extra = [(ErrorBody, "serialization"), (ProfileReport, "serialization")]
    _, document = models_json_schema([*models, *extra], schema_generator=Untitled)
    operations = [
        OperationModels(
            operation=name, request=request.__name__, result=result.__name__, available=ready, **_route(name)
        )
        for name, request, result, ready in rows
    ]
    return ServiceSchema.model_validate({"operations": operations, "$defs": document["$defs"]})


OPERATIONS = (
    *(data_status, data_calendar, data_securities, data_bars, data_issues, data_check, data_delistings),
    data_index_check,
    *(data_verify_submit, data_verify_list, data_resolve_list, data_resolve_approve, data_resolve_reject),
    *(expr_check, expr_eval),
    *(strategies.strategy_validate, strategies.strategy_summary, strategies.strategy_schema, strategies.strategy_save),
    *(strategies.strategy_list, strategies.strategy_versions, strategies.strategy_show, strategies.strategy_archive),
    screen,
    backtest,
    *(runs.runs_list, runs.runs_show, runs.runs_report, runs.runs_rerun, runs.runs_diff, runs.runs_orders),
    runs.runs_day,
    runs.runs_anomalies,
    explain,
    schema,
)


def _route(name: str) -> dict:
    method, path = route(name)
    return {"method": method, "path": path}


PLANNED = ()  # (name, request, result) of operations whose models are fixed before their command lands; none now
