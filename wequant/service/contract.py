"""schema: one JSON Schema document of every operation's request and result model and of Failure."""

from __future__ import annotations

from typing import get_type_hints

from pydantic import ConfigDict, Field, JsonValue
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from wequant.service.bars import data_bars
from wequant.service.base import Context, Failure, Model
from wequant.service.check import data_check
from wequant.service.expr import expr_check, expr_eval
from wequant.service.issues import data_issues
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


class ServiceSchema(Model):
    """A JSON Schema document: $defs holds every service model by name, in alphabetical order."""

    model_config = ConfigDict(serialize_by_alias=True)

    dialect: str = Field(DIALECT, alias="$schema")
    title: str = "WeQuant service models"
    description: str = f"Each operation's request and result, and Failure, the body of a failed one. From: {REGENERATE}"
    operations: list[OperationModels]
    """Each operation's request and result model, in the order of the commands."""
    defs: dict[str, JsonValue] = Field(alias="$defs")
    """Each model's JSON Schema by name."""


class Untitled(GenerateJsonSchema):
    """Field titles only repeat the field names, and TypeScript generators turn each into a numbered type alias."""

    def field_title_should_be_set(self, schema) -> bool:
        return False


def schema(request: SchemaRequest, context: Context = Context()) -> ServiceSchema:
    """Requests as the operations accept them, results and Failure as they print them; the same for the same code."""
    hints = [get_type_hints(operation) for operation in OPERATIONS]
    models = [(h["request"], "validation") for h in hints] + [(h["return"], "serialization") for h in hints]
    _, document = models_json_schema([*models, (Failure, "serialization")], schema_generator=Untitled)
    operations = [
        OperationModels(operation=operation.__name__, request=h["request"].__name__, result=h["return"].__name__)
        for operation, h in zip(OPERATIONS, hints)
    ]
    return ServiceSchema.model_validate({"operations": operations, "$defs": document["$defs"]})


OPERATIONS = (data_status, data_bars, data_issues, data_check, expr_check, expr_eval, schema)
