"""What a rebalance strategy holds and when: expressions and parameters, checked when built, with one canonical form."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import ClassVar, NamedTuple

from wequant.canonical import canonical
from wequant.data.codes import is_code
from wequant.expr import INDEXES, NOT, Expr, ExprError, Field
from wequant.strategy.checks import (
    Checked,
    Rule,
    choice,
    condition,
    count,
    date,
    expression,
    flag,
    instance,
    items,
    number_where,
    optional,
    wrong_type,
)
from wequant.strategy.compile import pool_expr, wrap
from wequant.strategy.errors import Loc, SpecError, SpecErrors
from wequant.strategy.form import parse_form, to_form

STRATEGY_VERSION = 2  # bump when a field's meaning, a default, the calendar or how a cross-section is picked changes
SH_SZ = NOT(Field("exchange@BJ"))  # the default universe: every code the rule book can trade
MODES = ("all", "any")
PREFERS = ("high", "low")
PERIODS = ("day", "week", "month")
WEIGHTINGS = ("equal", "score", "formula")
POOL_BOARDS = ("main", "chinext", "star")
POOL_ITEMS = ("index", "boards", "exclude_st", "min_list_days")
RESERVED = (
    *("code", "date", "universe", "scored", "score", "rank", "role", "weight", "targets", "backups", "formula"),
    *("listed", "bar", "pool", *POOL_ITEMS, "stocks", "held", "action"),
    *("sell_kind", "sell_step", "sell_rank", "sell_limit", "capped", "raw_weight"),
)


def _name(value: object) -> str:
    if not isinstance(value, str):
        raise wrong_type(value, "a str")
    if not value:
        raise SpecError("out_of_range", (), "a name is not empty")
    if value in RESERVED:
        raise SpecError("reserved_name", (), f"{value!r} is reserved ({', '.join(RESERVED)})")
    return value


def _boards(value: object) -> tuple[str, ...]:
    """Some of POOL_BOARDS, each once, in that order."""
    boards = items(str, "boards", 1)(value)
    for i, board in enumerate(boards):
        if board not in POOL_BOARDS:
            raise SpecError("not_a_choice", (i,), f"{board!r} is not one of {POOL_BOARDS}")
        if board in boards[:i]:
            raise SpecError("duplicate_value", (i,), f"{board!r} is given twice")
    return tuple(sorted(boards, key=POOL_BOARDS.index))


def _codes(value: object) -> tuple[str, ...]:
    """At least one code such as 600000.SH, each once, in the order given."""
    codes, seen = items(str, "codes", 1)(value), set()
    for i, code in enumerate(codes):
        if not is_code(code):
            raise SpecError("wrong_type", (i,), f"{code!r} is not a code such as 600000.SH")
        if code in seen:
            raise SpecError("duplicate_value", (i,), f"{code!r} is given twice")
        seen.add(code)
    return codes


def _label(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise wrong_type(value, "a non-empty str")
    return value


@dataclass(frozen=True)
class Condition(Checked):
    """A named bool expression a stock must pass at the decision day's close; a disabled one is kept but not applied."""

    name: str
    expr: Expr  # kind BOOL
    enabled: bool = True
    RULES: ClassVar[dict[str, Rule]] = {"name": _name, "expr": condition, "enabled": flag}


@dataclass(frozen=True)
class Group(Checked):
    """Named conditions of which all ("all") or any ("any") must pass; disabled conditions are left out."""

    name: str
    conditions: tuple[Condition, ...]
    mode: str = "all"
    enabled: bool = True
    RULES: ClassVar[dict[str, Rule]] = {
        "name": _name,
        "conditions": items(Condition, "conditions", 1),
        "mode": choice(MODES),
        "enabled": flag,
    }


@dataclass(frozen=True)
class Factor(Checked):
    """A ranking factor: its percentile among the scored stocks, the highest value best unless prefer is "low"."""

    name: str
    expr: Expr
    prefer: str = "high"
    weight: float = 1.0
    enabled: bool = True
    RULES: ClassVar[dict[str, Rule]] = {
        "name": _name,
        "expr": expression,
        "prefer": choice(PREFERS),
        "weight": number_where(lambda x: x > 0, "positive"),
        "enabled": flag,
    }


@dataclass(frozen=True)
class Stocks(Checked):
    """A fixed list of stocks (round 3), copied from a stock pool when the strategy is saved so that the strategy keeps
    it as it was: the codes in order, and the pool they came from (shown only)."""

    codes: tuple[str, ...]
    pool_id: str | None = None  # the stock pool's id in <home>/pools
    pool_name: str | None = None  # its name when copied
    RULES: ClassVar[dict[str, Rule]] = {"codes": _codes, "pool_id": optional(_label), "pool_name": optional(_label)}


@dataclass(frozen=True)
class Pool(Checked):
    """The items the pool adds to the universe, each a pass on the decision day; the defaults add none."""

    index: str | None = None  # one of INDEXES: a member of that index
    boards: tuple[str, ...] | None = None  # some of POOL_BOARDS, kept in that order: on one of them
    exclude_st: bool = False  # not ST
    min_list_days: int | None = None  # listed at least this many trading days, the day itself included
    stocks: Stocks | None = None  # on this fixed list; never together with index
    RULES: ClassVar[dict[str, Rule]] = {
        "index": optional(choice(INDEXES)),
        "boards": optional(_boards),
        "exclude_st": flag,
        "min_list_days": optional(count(2)),
        "stocks": optional(instance(Stocks)),
    }


class Step(NamedTuple):
    """A funnel step: a condition (expr) or a group (members combined by mode); chain steps narrow the funnel."""

    name: str
    expr: Expr | None
    members: tuple[str, ...]
    mode: str
    chain: bool


@dataclass(frozen=True)
class Strategy(Checked):
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
    hold_rank: int | None = None  # > top_n: a held stock stays a target while ranked this high; None: while in top_n
    max_weight: float | None = None  # in (0, 1): no target weighs more, the excess going to the others; None: no cap
    pool: Pool = Pool()  # items the pool adds to the universe
    # Later optional fields go after pool with defaults that keep today's behaviour; existing ones never change.
    RULES: ClassVar[dict[str, Rule]] = {
        "universe": condition,
        "filters": items(Condition | Group, "conditions and groups"),
        "factors": items(Factor, "factors"),
        "top_n": optional(count(1)),
        "backups": count(0),
        "period": choice(PERIODS),
        "every": count(1),
        "weighting": choice(WEIGHTINGS),
        "formula": optional(expression),
        "band": optional(number_where(lambda x: x >= 0, "at least 0")),
        "anchor": optional(date),
        "hold_rank": optional(count(1)),
        "max_weight": optional(number_where(lambda x: 0 < x < 1, "between 0 and 1, both excluded")),
        "pool": instance(Pool),
    }

    def check(self) -> None:
        """SpecErrors of every rule across fields that is broken."""
        if errors := [*_duplicates(self), *_selection(self), *_scope(self), *_compiled(self)]:
            raise SpecErrors(errors)

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


def _names(strategy: Strategy) -> Iterator[tuple[str, Loc]]:
    """Each condition, group and factor name with its location, in the form's order."""
    for i, item in enumerate(strategy.filters):
        yield item.name, ("filters", i, "name")
        for k, member in enumerate(getattr(item, "conditions", ())):
            yield member.name, ("filters", i, "conditions", k, "name")
    for i, factor in enumerate(strategy.factors):
        yield factor.name, ("factors", i, "name")


def _wrapped(strategy: Strategy) -> Iterator[tuple[Expr, Loc]]:
    """Each expression the compiler wraps in the pool, with its location: enabled conditions, factors and formula."""
    for i, item in enumerate(strategy.filters):
        if item.enabled and isinstance(item, Condition):
            yield item.expr, ("filters", i, "expr")
        elif item.enabled:
            for k, member in enumerate(item.conditions):
                if member.enabled:
                    yield member.expr, ("filters", i, "conditions", k, "expr")
    for i, factor in enumerate(strategy.factors):
        if factor.enabled:
            yield factor.expr, ("factors", i, "expr")
    if strategy.formula is not None:
        yield strategy.formula, ("formula",)


def _duplicates(strategy: Strategy) -> Iterator[SpecError]:
    seen = set()
    for name, loc in _names(strategy):
        if name in seen:
            yield SpecError("duplicate_name", loc, f"{name!r} names more than one condition, group or factor")
        seen.add(name)


def _selection(strategy: Strategy) -> Iterator[SpecError]:
    """How top_n, backups, weighting, formula and hold_rank depend on each other and on the factors."""
    ranked = any(f.enabled for f in strategy.factors)
    top_n = strategy.top_n
    if top_n is not None and not ranked:
        yield SpecError("requires", ("top_n",), "an int top_n needs an enabled factor")
    if strategy.backups and top_n is None:
        yield SpecError("requires", ("backups",), "backups need an int top_n")
    if strategy.weighting == "score" and not ranked:
        yield SpecError("requires", ("weighting",), "score weighting needs an enabled factor")
    if (strategy.weighting == "formula") != (strategy.formula is not None):
        yield SpecError("requires", ("formula",), "a formula is given exactly when weighting is 'formula'")
    if strategy.hold_rank is not None and top_n is None:
        yield SpecError("requires", ("hold_rank",), "hold_rank needs an int top_n")
    elif strategy.hold_rank is not None and strategy.hold_rank <= top_n:
        yield SpecError("out_of_range", ("hold_rank",), f"{strategy.hold_rank} is not above top_n {top_n}")


def _scope(strategy: Strategy) -> Iterator[SpecError]:
    """An index and a fixed list of stocks are two scopes: one at most."""
    if strategy.pool.index is not None and strategy.pool.stocks is not None:
        yield SpecError("requires", ("pool", "stocks"), "give at most one of pool index and pool stocks")


def _compiled(strategy: Strategy) -> Iterator[SpecError]:
    """too_large where the pool, copied into each cross-sectional operator, makes an expression exceed MAX_NODES."""
    try:
        pool = pool_expr(strategy)
    except ExprError as error:
        yield SpecError(error.code, ("pool",), str(error))
        return
    for expr, loc in _wrapped(strategy):
        try:
            wrap(expr, pool)
        except ExprError as error:
            yield SpecError(error.code, loc, str(error))
