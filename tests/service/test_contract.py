"""wequant schema: one JSON Schema document of every service model, and its snapshot for the frontend."""

from pathlib import Path

import pytest

from wequant import service
from wequant.service.contract import DIALECT, OPERATIONS, REGENERATE

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend/contract/service-models.schema.json"


@pytest.fixture(scope="module")
def document():
    return service.schema(service.SchemaRequest()).model_dump(mode="json")


def test_the_snapshot_is_the_output_of_the_schema_command(cli):
    code, out, err = cli("schema", "--json")
    assert (code, err) == (0, "")
    if out != SNAPSHOT.read_text("utf-8"):
        pytest.fail(f"{SNAPSHOT} is out of date; regenerate it in the same commit:\n    {REGENERATE}", pytrace=False)


def test_every_operation_is_listed_with_its_request_and_result(document):
    functions = {name for name in service.__all__ if name.islower()}
    listed = [row["operation"] for row in document["operations"]]
    assert listed == [operation.__name__ for operation in OPERATIONS] and set(listed) == functions
    for row in document["operations"]:
        assert {row["request"], row["result"]} <= set(service.__all__) & document["$defs"].keys()


def test_a_json_schema_document_of_every_model_by_name(document):
    defs = document["$defs"]
    assert list(document)[:2] == ["$schema", "title"] and document["$schema"] == DIALECT
    assert list(defs) == sorted(defs) and {"Failure", "Exit", "Row", "TableStatus", "IssueRow"} <= defs.keys()
    assert refs(document) <= {f"#/$defs/{name}" for name in defs}
    assert defs["Failure"]["required"] == ["exit", "message"] and defs["Exit"]["enum"] == [int(e) for e in service.Exit]


def test_requests_as_accepted_and_results_as_printed(document):
    request, result = document["$defs"]["BarsRequest"], document["$defs"]["Bars"]
    assert request["required"] == ["codes"] and request["additionalProperties"] is False
    assert result["required"] == ["data_version", "fields", "rows"]
    assert "title" not in request["properties"]["codes"] and request["title"] == "BarsRequest"


def test_human_output_lists_the_operations_then_the_models(cli):
    code, out, _ = cli("schema")
    operations, models = out.split("\n\n")
    assert code == 0 and operations.splitlines()[:2] == [
        "operation    request        result",
        "data_status  StatusRequest  DataStatus",
    ]
    assert models.splitlines()[0].split() == ["model", "description"]
    assert "\nFailure          A failed operation: the exit code its command ends with and why;" in models


def refs(node) -> set[str]:
    """Every $ref in a JSON document."""
    if isinstance(node, list):
        return set().union(*map(refs, node))
    if not isinstance(node, dict):
        return set()
    found = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
    return found.union(*map(refs, node.values()))
