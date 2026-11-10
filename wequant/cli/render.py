"""The human text of results and of the requests the command refuses."""

from __future__ import annotations

import json

from cyclopts import CycloptsError, CycloptsPanel
from pydantic import BaseModel, ValidationError

from wequant.service import ErrorBody, Exit
from wequant.service.details import pydantic_details


def render(result: BaseModel) -> str:
    """One 'name: value' line per set scalar field, then each list of rows as a table and each multi-line text."""
    lines, blocks = [], []
    for name, value in result.model_dump(mode="json").items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            blocks.append(table(value))
        elif isinstance(value, str) and "\n" in value:
            blocks.append(value.rstrip())
        elif value is not None:
            lines.append(f"{name}: {text(value)}")
    return "\n\n".join(["\n".join(lines), *blocks])


def render_schema(result: BaseModel) -> str:
    """The operations with their request and result models, then each model with its description's first line."""
    document = result.model_dump(mode="json")
    defs = document["$defs"].items()
    models = [{"model": name, "description": text(d.get("description")).split("\n")[0]} for name, d in defs]
    return "\n\n".join([table(document["operations"]), table(models)])


def table(rows: list[dict]) -> str:
    """Aligned columns with a header; a nested dict (Row.values) spreads into columns of its own."""
    flat = [_spread(row) for row in rows]
    cells = [list(flat[0]), *([text(value) for value in row.values()] for row in flat)]
    widths = [max(map(len, column)) for column in zip(*cells)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells)


def text(value: object) -> str:
    """'' for None, a string as it is, anything else as JSON."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _spread(row: dict) -> dict:
    flat = {}
    for key, value in row.items():
        flat.update(value if isinstance(value, dict) else {key: value})
    return flat


def refused(error: CycloptsError) -> ErrorBody:
    """The ErrorBody of a command line the parser refuses: one detail per pydantic error of the request, if any."""
    cause = error.__cause__
    details = pydantic_details(cause) if isinstance(cause, ValidationError) else []
    message = "; ".join(_reason(e) for e in cause.errors()) if details else str(error).strip()
    complete = not any(d.loc for d in details)
    return ErrorBody(exit=Exit.USAGE, name=Exit.USAGE.name, message=message, detail=details, complete=complete)


def error_panel(error: CycloptsError) -> object:
    """Cyclopts' error panel; a request that fails validation gets one line per error instead of pydantic's report."""
    if isinstance(error.__cause__, ValidationError):
        return CycloptsPanel("\n".join(_reason(e) for e in error.__cause__.errors()))
    return CycloptsPanel(error)


def _reason(error: dict) -> str:
    """'--option: message' of one pydantic error, or the message alone when it concerns the whole request."""
    message = error["msg"].removeprefix("Value error, ")
    return f"--{str(error['loc'][0]).replace('_', '-')}: {message}" if error["loc"] else message
