"""What a rebalance strategy holds and when: expressions and parameters, checked when built, with one canonical form."""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple

from wequant.canonical import canonical
from wequant.expr import BOOL, NOT, Expr, Field
from wequant.strategy.form import parse_form, to_form

STRATEGY_VERSION = 1  # bump when a field's meaning, a default, the calendar or how a cross-section is picked changes
SH_SZ = NOT(Field("exchange@BJ"))  # the default universe: every code the rule book can trade
MODES = ("all", "any")
PREFERS = ("high", "low")
PERIODS = ("day", "week", "month")
WEIGHTINGS = ("equal", "score", "formula")
RESERVED = ("code", "date", "universe", "scored", "score", "rank", "role", "weight", "targets", "backups", "formula")


@dataclass(frozen=True)
class Condition:
    """A named bool expression a stock must pass at the decision day's close; a disabled one is kept but not applied."""

    name: str
    expr: Expr  # kind BOOL
    enabled: bool = True

    def __post_init__(self) -> None:
        _check_name(self.name)
        _check_expr("expr", self.expr, BOOL)
        _check_flag("enabled", self.enabled)


@dataclass(frozen=True)
class Group:
    """Named conditions of which all ("all") or any ("any") must pass; disabled conditions are left out."""

    name: str
    conditions: tuple[Condition, ...]
    mode: str = "all"
    enabled: bool = True

    def __post_init__(self) -> None:
        _check_name(self.name)
        object.__setattr__(self, "conditions", _items("conditions", self.conditions, Condition, "conditions"))
        if not self.conditions:
            raise ValueError("conditions: a group needs at least one condition")
        _check_choice("mode", self.mode, MODES)
        _check_flag("enabled", self.enabled)


@dataclass(frozen=True)
class Factor:
    """A ranking factor: its percentile among the scored stocks, the highest value best unless prefer is "low"."""

    name: str
    expr: Expr
    prefer: str = "high"
    weight: float = 1.0
    enabled: bool = True

    def __post_init__(self) -> None:
        _check_name(self.name)
        _check_expr("expr", self.expr)
        _check_choice("prefer", self.prefer, PREFERS)
        object.__setattr__(self, "weight", _number("weight", self.weight))
        if self.weight <= 0:
            raise ValueError(f"weight: {self.weight!r} is not positive")
        _check_flag("enabled", self.enabled)


class Step(NamedTuple):
    """A funnel step: a condition (expr) or a group (members combined by mode); chain steps narrow the funnel."""

    name: str
    expr: Expr | None
    members: tuple[str, ...]
    mode: str
    chain: bool


@dataclass(frozen=True)
class Strategy:
    """Who may be held, how they rank, how many, how often and at what weights."""

    universe: Expr = SH_SZ  # kind BOOL
    filters: tuple[Condition | Group, ...] = ()  # every enabled one must pass, in this order
    factors: tuple[Factor, ...] = ()
    top_n: int | None = None  # None: every scored stock is a target
    backups: int = 0  # next-ranked stocks that stand in for new targets getting no shares
    period: str = "week"  # PERIODS
    every: int = 1  # every k-th trading day, week end or month end
    weighting: str = "equal"  # WEIGHTINGS
    formula: Expr | None = None  # given exactly when weighting is "formula"
    band: float | None = None  # None: a held target is never resized; b: resized when |value - goal| > b × goal
    anchor: dt.date | None = None  # a trading day the expressions count from; None: the panel's first day
    # Later optional fields go after anchor with defaults that keep today's behaviour; existing ones never change.

    def __post_init__(self) -> None:
        _check_expr("universe", self.universe, BOOL)
        object.__setattr__(self, "filters", _items("filters", self.filters, Condition | Group, "conditions and groups"))
        object.__setattr__(self, "factors", _items("factors", self.factors, Factor, "factors"))
        if self.band is not None:
            object.__setattr__(self, "band", _number("band", self.band))
        _check_unique(self)
        _check_selection(self)
        _check_calendar(self)

    def steps(self) -> tuple[Step, ...]:
        """The enabled filter steps in funnel order: a condition, or a group's enabled conditions then the group."""
        steps: list[Step] = []
        for item in (f for f in self.filters if f.enabled):
            if isinstance(item, Condition):
                steps.append(Step(item.name, item.expr, (), "all", True))
            elif members := [c for c in item.conditions if c.enabled]:
                steps += [Step(c.name, c.expr, (), "all", False) for c in members]
                steps.append(Step(item.name, None, tuple(c.name for c in members), item.mode, True))
        return tuple(steps)

    @property
    def form(self) -> dict:
        """to_form(self)."""
        return to_form(self)

    @property
    def key(self) -> str:
        """canonical(self.form)."""
        return canonical(self.form)

    @classmethod
    def from_form(cls, form: Mapping) -> Strategy:
        """parse_form(cls, form)."""
        return parse_form(cls, form)


def _check_unique(strategy: Strategy) -> None:
    names = [item.name for item in strategy.filters]
    names += [c.name for item in strategy.filters if isinstance(item, Group) for c in item.conditions]
    names += [f.name for f in strategy.factors]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise ValueError(f"name: {twice[0]!r} names more than one condition, group or factor")


def _check_selection(strategy: Strategy) -> None:
    """top_n, backups, weighting and formula, and how they depend on each other and on the factors."""
    ranked = any(f.enabled for f in strategy.factors)
    if strategy.top_n is not None:
        _check_count("top_n", strategy.top_n, 1)
        if not ranked:
            raise ValueError("top_n: an int top_n needs an enabled factor")
    _check_count("backups", strategy.backups, 0)
    if strategy.backups and strategy.top_n is None:
        raise ValueError("backups: backups need an int top_n")
    _check_choice("weighting", strategy.weighting, WEIGHTINGS)
    if strategy.weighting == "score" and not ranked:
        raise ValueError("weighting: score weighting needs an enabled factor")
    if (strategy.weighting == "formula") != (strategy.formula is not None):
        raise ValueError("formula: a formula is given exactly when weighting is 'formula'")
    if strategy.formula is not None:
        _check_expr("formula", strategy.formula)


def _check_calendar(strategy: Strategy) -> None:
    """period, every, band and anchor."""
    _check_choice("period", strategy.period, PERIODS)
    _check_count("every", strategy.every, 1)
    if strategy.band is not None and strategy.band < 0:
        raise ValueError(f"band: {strategy.band!r} is negative")
    if strategy.anchor is not None and (
        not isinstance(strategy.anchor, dt.date) or isinstance(strategy.anchor, dt.datetime)
    ):
        raise ValueError(f"anchor: {strategy.anchor!r} is not a date")


def _check_name(name: object) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError(f"name: {name!r} is not a non-empty str")
    if name in RESERVED:
        raise ValueError(f"name: {name!r} is reserved ({', '.join(RESERVED)})")


def _check_expr(field: str, value: object, kind: str | None = None) -> None:
    if not isinstance(value, Expr):
        raise ValueError(f"{field}: {value!r} is not an expression")
    if kind is not None and value.kind != kind:
        raise ValueError(f"{field}: {value!r} is a {value.kind} expression, not a {kind} one")


def _check_choice(field: str, value: object, choices: tuple[str, ...]) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{field}: {value!r} is not one of {choices}")


def _check_flag(field: str, value: object) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field}: {value!r} is not a bool")


def _check_count(field: str, value: object, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field}: {value!r} is not an int >= {minimum}")


def _number(field: str, value: object) -> float:
    """value as a float + 0.0; ValueError unless it is a finite int or float other than a bool."""
    # not abs(value) <= the largest float: true for NaN, ±inf and ints too large for a float
    if isinstance(value, bool) or not isinstance(value, int | float) or not abs(value) <= sys.float_info.max:
        raise ValueError(f"{field}: {value!r} is not a finite number")
    return float(value) + 0.0


def _items(field: str, value: object, kind: type, what: str) -> tuple:
    """value as a tuple; ValueError unless it is a list or tuple of kind."""
    if not isinstance(value, list | tuple) or not all(isinstance(item, kind) for item in value):
        raise ValueError(f"{field}: {value!r} is not a list of {what}")
    return tuple(value)
