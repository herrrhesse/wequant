"""Field rules of descriptions: each returns a field's value checked and normalised, or raises a SpecError located
inside the field."""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Callable
from typing import ClassVar

from wequant.expr import BOOL, Expr
from wequant.strategy.errors import SpecError

Rule = Callable[[object], object]


class Checked:
    """A frozen dataclass whose RULES check its fields one at a time, then check() looks across them."""

    RULES: ClassVar[dict[str, Rule]] = {}

    def __post_init__(self) -> None:
        for name in self.RULES:
            try:
                object.__setattr__(self, name, self.check_field(name, getattr(self, name)))
            except SpecError as error:
                raise error.under(name) from None
        self.check()

    def check(self) -> None:
        """The rules across fields: SpecErrors of those broken."""

    @classmethod
    def check_field(cls, name: str, value: object) -> object:
        """value checked and normalised by the rule of field name; a SpecError is located inside the field."""
        return cls.RULES[name](value) if name in cls.RULES else value


def wrong_type(value: object, what: str) -> SpecError:
    return SpecError("wrong_type", (), f"{value!r} is not {what}")


def flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise wrong_type(value, "a bool")
    return value


def choice(choices: tuple[str, ...]) -> Rule:
    """One of choices."""

    def rule(value: object) -> str:
        if not isinstance(value, str):
            raise wrong_type(value, "a str")
        if value not in choices:
            raise SpecError("not_a_choice", (), f"{value!r} is not one of {choices}")
        return value

    return rule


def count(minimum: int) -> Rule:
    """An int other than a bool, at least minimum."""

    def rule(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise wrong_type(value, "an int")
        if value < minimum:
            raise SpecError("out_of_range", (), f"{value!r} is not an int >= {minimum}")
        return value

    return rule


def number(value: object) -> float:
    """value as a float + 0.0: a finite int or float other than a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise wrong_type(value, "a number")
    if not abs(value) <= sys.float_info.max:  # False for NaN, ±inf and ints too large for a float
        raise SpecError("out_of_range", (), f"{value!r} is not a finite number")
    return float(value) + 0.0


def number_where(test: Callable[[float], bool], what: str) -> Rule:
    """A finite number for which test holds."""

    def rule(value: object) -> float:
        x = number(value)
        if not test(x):
            raise SpecError("out_of_range", (), f"{value!r} is not {what}")
        return x

    return rule


def date(value: object) -> dt.date:
    if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
        raise wrong_type(value, "a date")
    return value


def expression(value: object) -> Expr:
    if not isinstance(value, Expr):
        raise wrong_type(value, "an expression")
    return value


def condition(value: object) -> Expr:
    """A bool expression."""
    if expression(value).kind != BOOL:
        raise SpecError("needs_condition", (), f"{value!r} is a {value.kind} expression, not a condition")
    return value


def items(kind: type, what: str, minimum: int = 0) -> Rule:
    """A list or tuple of kind, as a tuple of at least minimum items."""

    def rule(value: object) -> tuple:
        if not isinstance(value, list | tuple) or not all(isinstance(item, kind) for item in value):
            raise wrong_type(value, f"a list of {what}")
        if len(value) < minimum:
            raise SpecError("out_of_range", (), f"needs at least {minimum} of {what}")
        return tuple(value)

    return rule


def instance(kind: type) -> Rule:
    """An instance of kind."""

    def rule(value: object) -> object:
        if not isinstance(value, kind):
            raise wrong_type(value, f"a {kind.__name__}")
        return value

    return rule


def optional(rule: Rule) -> Rule:
    """None, or a value rule accepts."""
    return lambda value: None if value is None else rule(value)
