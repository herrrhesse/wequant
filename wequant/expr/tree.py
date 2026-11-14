"""The expression tree: panel fields and operator calls, checked when built, with one canonical JSON form."""

from __future__ import annotations

import json
import math
import operator
from collections.abc import Iterable
from functools import cached_property

import numpy as np

from wequant.data.codes import is_code
from wequant.expr.vocab import (
    BOOL,
    DESCRIPTIVE,
    FAMILIES,
    FIELDS,
    FUTURE,
    NUM,
    OPERATORS,
    PLANNED,
    SAME,
    Signature,
)

MAX_NODES = 10_000  # nodes of the expanded tree: keeps keys and archived forms small
MAX_DEPTH = 256  # levels of the tree: keeps form, key and evaluation inside Python's default recursion limit
_MAX_BARS = 2**62  # far beyond any panel, and ops' int64 arithmetic on a count (EMA takes n + 1) cannot overflow


class ExprError(ValueError):
    """An expression that breaks a rule: a stable code, the 0-based argument of the call at fault (or None), and the
    path to the fault inside the form, where [name, arg0, arg1, ...] puts argument i at index i + 1."""

    def __init__(self, code: str, message: str, arg: int | None = None) -> None:
        super().__init__(message)
        self.code, self.arg = code, arg
        self.path: tuple[int, ...] = () if arg is None else (arg + 1,)


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

    def __delattr__(self, name: str) -> None:
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


class Codes(Expr):
    """A fixed list of stocks (round 3): a condition true on the bars of these codes, whatever the day."""

    name = "CODES"
    codes: tuple[str, ...]

    def __init__(self, codes: Iterable[str]) -> None:
        found = tuple(codes)
        for i, code in enumerate(found):
            if not isinstance(code, str) or not is_code(code):
                raise ExprError("not_a_form", f"argument {i} of CODES is not a code such as 600000.SH: {code!r}", i)
        if not found:
            raise ExprError("bad_arity", "CODES takes at least one code")
        vars(self).update(codes=tuple(sorted(set(found))), kind=BOOL, size=1, depth=1)

    @property
    def form(self) -> list:
        return [self.name, *self.codes]

    def __repr__(self) -> str:
        return f"CODES({len(self.codes)} codes)"


def from_form(form: str | list) -> Expr:
    """The expression of a canonical form, built and checked exactly as in Python."""
    return _from_form(form, 1)


def _from_form(form: object, level: int) -> Expr:
    """form at its level of nesting; deeper than MAX_DEPTH stops here, before Python's recursion limit does."""
    if level > MAX_DEPTH:
        raise ExprError("too_deep", f"the form is deeper than {MAX_DEPTH} levels")
    if isinstance(form, str):
        return Field(form)
    if isinstance(form, list) and form and form[0] == Codes.name:
        return Codes(form[1:])
    if isinstance(form, list) and form and isinstance(form[0], str):
        return Op(form[0], *[_argument_form(a, i, level) for i, a in enumerate(form[1:], 1)])
    raise ExprError("not_a_form", f"not an expression form: {form!r}")


def _argument_form(form: object, i: int, level: int) -> object:
    """The argument at index i of a call's form: an Expr built from a form, else the value as it is; an error inside it
    is located under i."""
    if not isinstance(form, str | list):
        return form
    try:
        return _from_form(form, level + 1)
    except ExprError as error:
        error.path = (i, *error.path)
        raise


def _field_kind(name: str) -> str:
    if not isinstance(name, str):
        raise ExprError("not_a_form", f"a field name is a string, not {name!r}")
    if name in DESCRIPTIVE:
        raise LookAheadError("descriptive_field", f"{name} is a descriptive day status that a later bar can change")
    if name in FUTURE:
        raise _future(name)
    if name in PLANNED:
        raise ExprError("not_available", f"{name} is not available yet: {PLANNED[name]}")
    if name in FIELDS:
        return FIELDS[name].kind
    family, _, param = name.partition("@")
    if family in FAMILIES and param in FAMILIES[family].params:
        return FAMILIES[family].kind
    families = ", ".join(f"{f}@{p}" for f, spec in FAMILIES.items() for p in spec.params)
    raise ExprError("unknown_field", f"unknown field {name!r}; fields: {', '.join(FIELDS)}; families: {families}")


def _future(name: str) -> LookAheadError:
    return LookAheadError("future_function", f"{name} is a future function: it reads bars after day t")


def _signature(name: str, arity: int) -> Signature:
    if name in FUTURE:
        raise _future(name)
    if name not in OPERATORS:
        raise ExprError("unknown_operator", f"unknown operator {name!r}")
    signature = OPERATORS[name]
    if arity != len(signature.roles):
        raise ExprError("bad_arity", f"{name} takes {len(signature.roles)} arguments, not {arity}")
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
    number = _float(value)
    if number is None or not math.isfinite(number):
        message = f"argument {i} of {op} must be a series, a finite number or None, not {value!r}"
        raise ExprError("bad_constant", message, i)
    return number + 0.0  # -0.0 and 0.0 must share one canonical form


def _series(op: str, i: int, value: object) -> Expr:
    if not isinstance(value, Expr):
        raise ExprError("needs_series", f"argument {i} of {op} must be a series, not {value!r}", i)
    return value


def _condition(op: str, i: int, value: object) -> Expr:
    if not (isinstance(value, Expr) and value.kind == BOOL):
        raise ExprError("needs_condition", f"argument {i} of {op} must be a condition, not the number {value!r}", i)
    return value


def _count(op: str, i: int, value: object, minimum: int) -> int:
    n = _integer(value)
    if n is None:
        raise ExprError("needs_integer", f"argument {i} of {op} must be an integer constant, not {value!r}", i)
    if n < 0:
        raise LookAheadError("negative_window", f"{op} with {n} bars would read bars after day t", i)
    if n < minimum:
        raise ExprError("out_of_range", f"argument {i} of {op} must be at least {minimum}, not {n}", i)
    if n > _MAX_BARS:
        raise ExprError("out_of_range", f"argument {i} of {op} must be at most {_MAX_BARS}, not {n}", i)
    return n


def _weight(op: str, i: int, value: object, n: int) -> int:
    w = _integer(value)
    if w is None or not 1 <= w <= n:
        message = f"argument {i} of {op} must be an integer constant from 1 to {n}, not {value!r}"
        raise ExprError("needs_integer" if w is None else "out_of_range", message, i)
    return w


def _integer(value: object) -> int | None:
    """value as an exact int when it is an integer (bool and timedelta64 are not), else None."""
    if isinstance(value, int | np.integer) and not isinstance(value, bool | np.timedelta64):
        return operator.index(value)  # the true value, whatever an int subclass says of itself
    return None


def _float(value: object) -> float | None:
    """value as a float when it is a number (bool and timedelta64 are not), inf for an int beyond floats, else None."""
    if not isinstance(value, int | float | np.integer | np.floating) or isinstance(value, bool | np.timedelta64):
        return None
    try:
        return float(value)
    except OverflowError:
        return math.inf


def _measure(op: str, children: list[Expr]) -> tuple[int, int]:
    """Size and depth of a node over its Expr arguments, each within its limit."""
    if not children:
        raise ExprError("needs_series", f"{op} needs at least one series argument")
    size, depth = 1 + sum(c.size for c in children), 1 + max(c.depth for c in children)
    if size > MAX_NODES:
        raise ExprError("too_large", f"{op}: the expression would have more than {MAX_NODES} nodes")
    if depth > MAX_DEPTH:
        raise ExprError("too_deep", f"{op}: the expression would be deeper than {MAX_DEPTH} levels")
    return size, depth


def _kind(signature: Signature, args: list) -> str:
    """The result kind; SAME is bool when every value or series argument is a bool Expr or None."""
    if signature.result != SAME:
        return signature.result
    values = [a for role, a in zip(signature.roles, args) if role in ("x", "s")]
    return BOOL if all(a is None or (isinstance(a, Expr) and a.kind == BOOL) for a in values) else NUM
