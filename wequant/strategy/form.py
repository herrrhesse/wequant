"""Canonical forms of descriptions: plain JSON with defaults left out, and back."""

from __future__ import annotations

import datetime as dt
import types
import typing
from dataclasses import MISSING, Field, fields, is_dataclass
from functools import cache
from typing import get_args, get_origin, get_type_hints

from wequant.expr import Expr, ExprError, from_form
from wequant.strategy.errors import Collector, SpecError, SpecErrors


def to_form(value: object) -> object:
    """value as plain JSON: a dataclass as its fields in order without those equal to their defaults (Execution and
    MetricSpec always in full, as to_dict()), Expr as its form, tuples as lists, dates as ISO text, floats + 0.0."""
    if isinstance(value, Expr):
        return value.form
    if _pinned(value):
        return {name: to_form(v) for name, v in value.to_dict().items()}
    if is_dataclass(value):
        pairs = ((f, getattr(value, f.name)) for f in fields(value))
        return {f.name: to_form(v) for f, v in pairs if not _is_default(f, v)}
    if isinstance(value, tuple | list):
        return [to_form(v) for v in value]
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return value + 0.0
    return value


def parse_form[T](cls: type[T], form: object) -> T:
    """The cls a form describes, each field read by its annotation and checked by cls.check_field when cls has one.

    Unknown keys and field errors in the form's order, then missing keys, are raised at once as SpecErrors, each located
    in the form; the rules across fields follow, as SpecErrors of those broken.
    """
    if not isinstance(form, dict):
        raise SpecError("wrong_type", (), f"{cls.__name__}: the form is a {type(form).__name__}, not an object")
    declared, found, values = {f.name: f for f in fields(cls)}, Collector(), {}
    for key, value in form.items():
        with found.at(key):
            values[key] = _field(cls, declared, key, value)
    for name in (name for name, f in declared.items() if name not in form and _required(f)):
        found.errors.append(SpecError("missing_key", (name,), f"{cls.__name__} needs {name!r}"))
    found.check()
    try:
        return cls(**values)
    except SpecError as error:  # a rule across fields of a class whose check raises one error
        raise SpecErrors([error]) from None


def _field(cls: type, declared: dict[str, Field], name: str, value: object) -> object:
    """value of field name read by its annotation, then checked by cls.check_field when cls has one."""
    if name not in declared:
        raise SpecError("unknown_key", (), f"{cls.__name__} has no field {name!r}")
    value = _read(_hints(cls)[name], value)
    check = getattr(cls, "check_field", None)
    return value if check is None else check(name, value)


@cache
def _hints(cls: type) -> dict[str, object]:
    return get_type_hints(cls)


def _read(hint: object, value: object) -> object:
    """value read as hint: an Expr from its form, a union option, a tuple, a dataclass, a date or a plain value."""
    if hint is Expr:
        return _expr(value)
    if get_origin(hint) in (types.UnionType, typing.Union):
        return _option(get_args(hint), value)
    if get_origin(hint) is tuple:
        return _tuple(get_args(hint)[0], value)
    if is_dataclass(hint):
        return parse_form(hint, value)
    if hint is dt.date:
        return _date(value)
    if hint is float and isinstance(value, int | float) and not isinstance(value, bool):
        return _float(value)
    if type(value) is hint:
        return value
    raise SpecError("wrong_type", (), f"{value!r} is not of type {getattr(hint, '__name__', hint)}")


def _expr(form: object) -> Expr:
    """The expression of a form; its ExprError becomes a SpecError with the same code and path."""
    try:
        return from_form(form)
    except ExprError as error:
        raise SpecError(error.code, (), str(error), error.path) from None


def _option(options: tuple, value: object) -> object:
    """value read as one option of a union: None, the one other type, or the dataclass with fewest unknown keys."""
    if value is None and type(None) in options:
        return None
    kinds = [option for option in options if option is not type(None)]
    if len(kinds) == 1:
        return _read(kinds[0], value)
    if not isinstance(value, dict):
        raise SpecError("wrong_type", (), f"{value!r} is none of {[kind.__name__ for kind in kinds]}")
    return parse_form(min(kinds, key=lambda kind: len(value.keys() - {f.name for f in fields(kind)})), value)


def _tuple(kind: object, value: object) -> tuple:
    """A list read item by item as kind; every item's errors raised at once."""
    if not isinstance(value, list):
        raise SpecError("wrong_type", (), f"{value!r} is not a list")
    found, read = Collector(), []
    for i, item in enumerate(value):
        with found.at(i):
            read.append(_read(kind, item))
    found.check()
    return tuple(read)


def _date(value: object) -> dt.date:
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise SpecError("wrong_type", (), f"{value!r} is not an ISO date")


def _float(value: int | float) -> float:
    try:
        return float(value)
    except OverflowError:
        raise SpecError("out_of_range", (), f"{value!r} is too large for a float") from None


def _pinned(value: object) -> bool:
    """Execution and MetricSpec: conventions whose defaults may change, so a form always holds them in full."""
    return is_dataclass(value) and hasattr(value, "to_dict")


def _is_default(field: Field, value: object) -> bool:
    return field.default is not MISSING and not _pinned(value) and value == field.default


def _required(field: Field) -> bool:
    return field.default is MISSING and field.default_factory is MISSING
