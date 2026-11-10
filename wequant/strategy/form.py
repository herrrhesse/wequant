"""Canonical forms of descriptions: plain JSON with defaults left out, and back."""

from __future__ import annotations

import datetime as dt
import types
import typing
from dataclasses import MISSING, Field, fields, is_dataclass
from typing import get_args, get_origin, get_type_hints

from wequant.expr import Expr, from_form


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
    """The cls a form describes, each field read by its annotation and checked by cls; ValueError for an unknown field
    or a wrong type."""
    if not isinstance(form, dict):
        raise ValueError(f"{cls.__name__}: the form is a {type(form).__name__}, not an object")
    declared = {f.name: f for f in fields(cls)}
    if unknown := [key for key in form if key not in declared]:
        raise ValueError(f"{cls.__name__}: unknown fields {unknown}")
    if missing := [name for name, f in declared.items() if name not in form and _required(f)]:
        raise ValueError(f"{cls.__name__}: missing fields {missing}")
    hints = get_type_hints(cls)
    return cls(**{key: _read(f"{cls.__name__}.{key}", hints[key], value) for key, value in form.items()})


def _read(field: str, hint: object, value: object) -> object:
    """value read as hint: an Expr from its form, a union option, a tuple, a dataclass, a date or a plain value."""
    if hint is Expr:
        return from_form(value)
    if get_origin(hint) in (types.UnionType, typing.Union):
        return _option(field, get_args(hint), value)
    if get_origin(hint) is tuple:
        if not isinstance(value, list):
            raise ValueError(f"{field}: {value!r} is not a list")
        return tuple(_read(field, get_args(hint)[0], item) for item in value)
    if is_dataclass(hint):
        return parse_form(hint, value)
    if hint is dt.date:
        return _date(field, value)
    if hint is float and isinstance(value, int | float) and not isinstance(value, bool):
        return _float(field, value)
    if type(value) is hint:
        return value
    raise ValueError(f"{field}: {value!r} is not a {getattr(hint, '__name__', hint)}")


def _option(field: str, options: tuple, value: object) -> object:
    """value read as one option of a union: None, the one other type, or the first dataclass whose fields hold every key."""
    if value is None and type(None) in options:
        return None
    kinds = [option for option in options if option is not type(None)]
    if len(kinds) == 1:
        return _read(field, kinds[0], value)
    for kind in kinds:  # Condition | Group: an object with a "conditions" key is a Group
        if isinstance(value, dict) and value.keys() <= {f.name for f in fields(kind)}:
            return parse_form(kind, value)
    raise ValueError(f"{field}: {value!r} is none of {[kind.__name__ for kind in kinds]}")


def _date(field: str, value: object) -> dt.date:
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{field}: {value!r} is not an ISO date")


def _float(field: str, value: int | float) -> float:
    try:
        return float(value)
    except OverflowError:
        raise ValueError(f"{field}: {value!r} is too large for a float") from None


def _pinned(value: object) -> bool:
    """Execution and MetricSpec: conventions whose defaults may change, so a form always holds them in full."""
    return is_dataclass(value) and hasattr(value, "to_dict")


def _is_default(field: Field, value: object) -> bool:
    return field.default is not MISSING and not _pinned(value) and value == field.default


def _required(field: Field) -> bool:
    return field.default is MISSING and field.default_factory is MISSING
