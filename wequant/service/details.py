"""Located reasons (design 7.3): core and pydantic errors as ErrorDetails, carried by a ServiceError."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ValidationError

from wequant.expr import ExprError
from wequant.service.base import ErrorDetail
from wequant.service.errors import Exit, ServiceError
from wequant.strategy import SpecError, SpecErrors, Strategy

Loc = Sequence[str | int]
PYDANTIC = {
    "extra_forbidden": "unknown_key",
    "missing": "missing_key",
    "literal_error": "not_a_choice",
    "enum": "not_a_choice",
    **dict.fromkeys(("greater_than", "greater_than_equal", "less_than", "less_than_equal"), "out_of_range"),
    **dict.fromkeys(("too_short", "too_long", "finite_number"), "out_of_range"),
}
OWN = ("requires", "duplicate_value", "not_a_form", "out_of_range", "wrong_type")  # raised as PydanticCustomError


def pydantic_details(error: ValidationError, loc: Loc = ()) -> list[ErrorDetail]:
    """One detail per pydantic error; a type without a code of its own is wrong_type."""
    return [
        ErrorDetail(
            code=PYDANTIC.get(e["type"], e["type"] if e["type"] in OWN else "wrong_type"),
            message=e["msg"].removeprefix("Value error, "),
            loc=[*loc, *e["loc"]],
        )
        for e in error.errors()
    ]


def spec_details(error: SpecError | SpecErrors, loc: Loc = ()) -> list[ErrorDetail]:
    """One detail per field error of a description."""
    return [ErrorDetail(code=e.code, message=e.message, loc=[*loc, *e.loc], path=list(e.path)) for e in error.errors]


def expr_detail(error: ExprError, loc: Loc = ()) -> ErrorDetail:
    """The detail of a refused expression."""
    return ErrorDetail(code=error.code, message=str(error), loc=list(loc), path=list(error.path))


def usage(details: list[ErrorDetail]) -> ServiceError:
    """USAGE carrying details; the message is the first one's, located."""
    first, more = details[0], f" (and {len(details) - 1} more)" if len(details) > 1 else ""
    where = ".".join(map(str, first.loc))
    return ServiceError(Exit.USAGE, (f"{where}: " if where else "") + first.message + more, details)


def validated[M: BaseModel](model: type[M], data: Mapping) -> M:
    """model built from data; USAGE with a detail per error otherwise."""
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise usage(pydantic_details(error)) from None


def to_strategy(spec: BaseModel, loc: Loc = ("strategy",)) -> Strategy:
    """The core strategy of a StrategySpec; USAGE with every field error located under loc."""
    try:
        return Strategy.from_form(spec.model_dump(mode="json"))
    except (SpecError, SpecErrors) as error:
        raise usage(spec_details(error, loc)) from None
