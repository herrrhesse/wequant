"""The expression tree: panel fields and operator calls, checked when built, with one canonical JSON form."""

from __future__ import annotations

import json
import math
from functools import cached_property

import numpy as np

from wequant.expr.vocab import BOOL, DESCRIPTIVE, FAMILIES, FIELDS, FUTURE, NUM, OPERATORS, SAME, Signature

MAX_NODES = 10_000  # nodes of the expanded tree: keeps keys and archived forms small
MAX_DEPTH = 256  # levels of the tree: keeps form, key and evaluation inside Python's default recursion limit


class ExprError(ValueError):
    """An expression that breaks a rule; arg is the 0-based argument of the call at fault, or None."""

    def __init__(self, message: str, arg: int | None = None) -> None:
        super().__init__(message)
        self.arg = arg


class LookAheadError(ExprError):
    """An expression that would read data from after the day its value is for."""


class Expr:
    """An immutable checked node; nodes are equal when their canonical forms are."""

    kind: str
    size: int
    depth: int
    __array_ufunc__ = None

    def __setattr__(self, name: str, value: object) -> None:
        """AttributeError: a node is hashed and cached by its key, so it never changes after __init__."""
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def form(self) -> str | list:
        """The canonical form: a field name, or [operator, *arguments]; a new JSON-able object."""
        raise NotImplementedError

    @cached_property
    def key(self) -> str:
        """The canonical form as compact JSON text: the cache key and the archived form."""
        return json.dumps(self.form, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    def __eq__(self, other: object) -> bool:
        """Same key; use EQ for cell-by-cell equality."""
        return self.key == other.key if isinstance(other, Expr) else NotImplemented

    def __hash__(self) -> int:
        return hash(self.key)

    def __bool__(self) -> bool:
        """TypeError: and, or, not and chained comparisons would drop part of an expression."""
        raise TypeError("an expression has no truth value: use &, |, ~ and one comparison at a time")

    def __add__(self, other: Expr | float | None) -> Op:
        return Op("ADD", self, other)

    def __radd__(self, other: Expr | float | None) -> Op:
        return Op("ADD", other, self)

    def __sub__(self, other: Expr | float | None) -> Op:
        return Op("SUB", self, other)

    def __rsub__(self, other: Expr | float | None) -> Op:
        return Op("SUB", other, self)

    def __mul__(self, other: Expr | float | None) -> Op:
        return Op("MUL", self, other)

    def __rmul__(self, other: Expr | float | None) -> Op:
        return Op("MUL", other, self)

    def __truediv__(self, other: Expr | float | None) -> Op:
        return Op("DIV", self, other)

    def __rtruediv__(self, other: Expr | float | None) -> Op:
        return Op("DIV", other, self)

    def __neg__(self) -> Op:
        return Op("SUB", 0.0, self)

    def __abs__(self) -> Op:
        return Op("ABS", self)

    def __gt__(self, other: Expr | float | None) -> Op:
        return Op("GT", self, other)

    def __ge__(self, other: Expr | float | None) -> Op:
        return Op("GE", self, other)

    def __lt__(self, other: Expr | float | None) -> Op:
        return Op("LT", self, other)

    def __le__(self, other: Expr | float | None) -> Op:
        return Op("LE", self, other)

    def __and__(self, other: Expr) -> Op:
        return Op("AND", self, other)

    def __or__(self, other: Expr) -> Op:
        return Op("OR", self, other)

    def __invert__(self) -> Op:
        return Op("NOT", self)


class Field(Expr):
    """A panel field known at day t's close: a FIELDS name or a FAMILIES name@param."""

    name: str

    def __init__(self, name: str) -> None:
        vars(self).update(name=name, kind=_field_kind(name), size=1, depth=1)

    @property
    def form(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return self.name


class Op(Expr):
    """A wequant.ops operator applied to arguments, checked against its Signature when built."""

    name: str
    args: tuple[Expr | float | int | None, ...]

    def __init__(self, name: str, *args: Expr | int | float | None) -> None:
        signature = _signature(name, len(args))
        checked: list = []
        for i, (role, value) in enumerate(zip(signature.roles, args)):
            checked.append(_argument(name, i, role, value, checked))
        size, depth = _measure(name, [a for a in checked if isinstance(a, Expr)])
        kind = _kind(signature, checked)
        vars(self).update(name=name, args=tuple(checked), kind=kind, size=size, depth=depth)

    @property
    def form(self) -> list:
        return [self.name, *[a.form if isinstance(a, Expr) else a for a in self.args]]

    def __repr__(self) -> str:
        return f"{self.name}({', '.join([repr(a) for a in self.args])})"


def from_form(form: str | list) -> Expr:
    """The expression of a canonical form, built and checked exactly as in Python."""
    if isinstance(form, str):
        return Field(form)
    if isinstance(form, list) and form and isinstance(form[0], str):
        return Op(form[0], *[from_form(a) if isinstance(a, str | list) else a for a in form[1:]])
    raise ExprError(f"not an expression form: {form!r}")


def _field_kind(name: str) -> str:
    if not isinstance(name, str):
        raise ExprError(f"a field name is a string, not {name!r}")
    if name in DESCRIPTIVE:
        raise LookAheadError(f"{name} is a descriptive day status that a later bar can change")
    if name in FIELDS:
        return FIELDS[name].kind
    family, _, param = name.partition("@")
    if family in FAMILIES and param in FAMILIES[family].params:
        return FAMILIES[family].kind
    families = ", ".join(f"{f}@{p}" for f, spec in FAMILIES.items() for p in spec.params)
    raise ExprError(f"unknown field {name!r}; fields: {', '.join(FIELDS)}; families: {families}")


def _signature(name: str, arity: int) -> Signature:
    if name in FUTURE:
        raise LookAheadError(f"{name} is a future function: it reads bars after day t")
    if name not in OPERATORS:
        raise ExprError(f"unknown operator {name!r}")
    signature = OPERATORS[name]
    if arity != len(signature.roles):
        raise ExprError(f"{name} takes {len(signature.roles)} arguments, not {arity}")
    return signature


def _argument(op: str, i: int, role: str, value: object, before: list) -> Expr | float | int | None:
    """value checked against its role; before holds the arguments already checked."""
    if role == "x":
        return value if value is None or isinstance(value, Expr) else _constant(op, i, value)
    if role == "s":
        return _series(op, i, value)
    if role == "c":
        return _condition(op, i, value)
    if role == "w":
        return _weight(op, i, value, before[-1])
    return _count(op, i, value, int(role[1]))


def _constant(op: str, i: int, value: object) -> float:
    number = isinstance(value, int | float | np.integer | np.floating) and not isinstance(value, bool)
    if not number or not math.isfinite(value):
        raise ExprError(f"argument {i} of {op} must be a series, a finite number or None, not {value!r}", i)
    return float(value) + 0.0  # -0.0 and 0.0 must share one canonical form


def _series(op: str, i: int, value: object) -> Expr:
    if not isinstance(value, Expr):
        raise ExprError(f"argument {i} of {op} must be a series, not {value!r}", i)
    return value


def _condition(op: str, i: int, value: object) -> Expr:
    if not (isinstance(value, Expr) and value.kind == BOOL):
        raise ExprError(f"argument {i} of {op} must be a condition, not the number {value!r}", i)
    return value


def _count(op: str, i: int, value: object, minimum: int) -> int:
    if not _is_integer(value):
        raise ExprError(f"argument {i} of {op} must be an integer constant, not {value!r}", i)
    if value < 0:
        raise LookAheadError(f"{op} with {value} bars would read bars after day t", i)
    if value < minimum:
        raise ExprError(f"argument {i} of {op} must be at least {minimum}, not {value}", i)
    return int(value)


def _weight(op: str, i: int, value: object, n: int) -> int:
    if not _is_integer(value) or not 1 <= value <= n:
        raise ExprError(f"argument {i} of {op} must be an integer constant from 1 to {n}, not {value!r}", i)
    return int(value)


def _is_integer(value: object) -> bool:
    return isinstance(value, int | np.integer) and not isinstance(value, bool)


def _measure(op: str, children: list[Expr]) -> tuple[int, int]:
    """Size and depth of a node over its Expr arguments, each within its limit."""
    if not children:
        raise ExprError(f"{op} needs at least one series argument")
    size, depth = 1 + sum(c.size for c in children), 1 + max(c.depth for c in children)
    if size > MAX_NODES:
        raise ExprError(f"{op}: the expression would have more than {MAX_NODES} nodes")
    if depth > MAX_DEPTH:
        raise ExprError(f"{op}: the expression would be deeper than {MAX_DEPTH} levels")
    return size, depth


def _kind(signature: Signature, args: list) -> str:
    """The result kind; SAME is bool when every value or series argument is a bool Expr or None."""
    if signature.result != SAME:
        return signature.result
    values = [a for role, a in zip(signature.roles, args) if role in ("x", "s")]
    return BOOL if all(a is None or (isinstance(a, Expr) and a.kind == BOOL) for a in values) else NUM
