"""schema: one JSON Schema document of every operation's request and result model and of ErrorBody."""

from __future__ import annotations

from typing import get_type_hints

from pydantic import ConfigDict, Field, JsonValue
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from wequant.service import backtest, delistings, explain, index_check, runs, screen, strategies
from wequant.service.bars import data_bars
from wequant.service.base import Context, ErrorBody, Model
from wequant.service.check import data_check
from wequant.service.expr import expr_check, expr_eval
from wequant.service.issues import data_issues
from wequant.service.profile import ProfileReport
from wequant.service.status import data_status

DIALECT = "https://json-schema.org/draft/2020-12/schema"
REGENERATE = "uv run wequant schema --json > frontend/contract/service-models.schema.json"


class SchemaRequest(Model):
    """The JSON Schema of every service model."""


class OperationModels(Model):
    """One operation and the names of its request and result models."""

    operation: str
    """The function in wequant.service, e.g. data_bars for wequant data bars."""
    request: str
    result: str
    available: bool
    """False: its models are fixed and its command is still to come."""


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
        OperationModels(operation=name, request=request.__name__, result=result.__name__, available=ready)
        for name, request, result, ready in rows
    ]
    return ServiceSchema.model_validate({"operations": operations, "$defs": document["$defs"]})


OPERATIONS = (data_status, data_bars, data_issues, data_check, expr_check, expr_eval, schema)

PLANNED = (  # operations whose models are fixed for the frontend; each moves to OPERATIONS when its command lands
    ("strategy_validate", strategies.StrategyRequest, strategies.StrategyCheck),
    ("strategy_summary", strategies.StrategyRequest, strategies.StrategySummary),
    ("strategy_schema", strategies.StrategySchemaRequest, strategies.StrategySchema),
    ("strategy_save", strategies.SaveRequest, strategies.SavedVersion),
    ("strategy_list", strategies.StrategyListRequest, strategies.StrategyList),
    ("strategy_versions", strategies.VersionsRequest, strategies.StrategyVersions),
    ("strategy_show", strategies.ShowRequest, strategies.SavedStrategy),
    ("strategy_archive", strategies.ArchiveRequest, strategies.StrategyInfo),
    ("screen", screen.ScreenRequest, screen.ScreenResult),
    ("backtest", backtest.BacktestRequest, backtest.BacktestResult),
    ("runs_list", runs.RunsListRequest, runs.RunsPage),
    ("runs_show", runs.RunRequest, runs.RunDetail),
    ("runs_rerun", runs.RunRequest, runs.RunComparison),
    ("runs_diff", runs.RunDiffRequest, runs.RunDiff),
    ("runs_orders", runs.OrdersRequest, runs.OrdersPage),
    ("runs_day", runs.RunDayRequest, runs.RunDay),
    ("runs_anomalies", runs.AnomaliesRequest, runs.AnomaliesPage),
    ("explain", explain.ExplainRequest, explain.Explain),
    ("data_delistings", delistings.DelistingsRequest, delistings.Delistings),
    ("data_index_check", index_check.IndexCheckRequest, index_check.IndexCheck),
)
