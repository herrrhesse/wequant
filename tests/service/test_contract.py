"""wequant schema: one JSON Schema document of every service model, and its snapshot for the frontend."""

from pathlib import Path

import pytest

from wequant import service
from wequant.service.contract import OPERATIONS, PLANNED, REGENERATE

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend/contract/service-models.schema.json"


@pytest.fixture(scope="module")
def document():
    return service.schema(service.SchemaRequest()).model_dump(mode="json")


def test_the_snapshot_is_the_output_of_the_schema_command(cli):
    """The frontend's contract file is exactly what `wequant schema --json` prints for the current models."""
    code, out, err = cli("schema", "--json")
    assert (code, err) == (0, "")
    if out != SNAPSHOT.read_text("utf-8"):
        pytest.fail(f"{SNAPSHOT} is out of date; regenerate it in the same commit:\n    {REGENERATE}", pytrace=False)


def test_every_operation_is_listed_with_its_request_and_result(document):
    """Available operations are exactly the service functions; planned ones already fix their models (S3.6)."""
    functions = {name for name in service.__all__ if name.islower()}
    available = [row["operation"] for row in document["operations"] if row["available"]]
    planned = [row["operation"] for row in document["operations"] if not row["available"]]
    assert available == [operation.__name__ for operation in OPERATIONS] and set(available) == functions
    assert planned == [name for name, _, _ in PLANNED] and not set(planned) & functions
    for row in document["operations"]:
        assert {row["request"], row["result"]} <= document["$defs"].keys()
