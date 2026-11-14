"""What an expression may name: panel fields known at day t's close, and the operators of wequant.ops."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from wequant import ops
from wequant.data.codes import BOARDS, EXCHANGES
from wequant.data.indexes import INDEXES, STORED_AS

if TYPE_CHECKING:
    from wequant.panel import Panel

EXPR_VERSION = 1  # bump when an operator, a field or the canonical form changes meaning
NUM, BOOL, SAME = "num", "bool", "same"


class FieldSpec(NamedTuple):
    """A panel field: its kind and how to read it as a (T, N) float64 grid, NaN where the stock has no bar."""

    kind: str
    load: Callable[[Panel], np.ndarray]


class Family(NamedTuple):
    """A parameterised field name@param: its kind, its parameters and how to read it as a (T, N) grid for one of them."""

    kind: str
    params: tuple[str, ...]
    load: Callable[[Panel, str], np.ndarray]


class Signature(NamedTuple):
    """An operator: its wequant.ops function, the role of each argument and the kind of its result."""

    function: Callable[..., np.ndarray]
    roles: tuple[str, ...]
    result: str


def _raw(name: str) -> FieldSpec:
    return FieldSpec(NUM, lambda p: p.raw(name))


def _adjusted(name: str) -> FieldSpec:
    return FieldSpec(NUM, lambda p: p.adjusted(name))


def _limit(name: str) -> FieldSpec:
    return FieldSpec(NUM, lambda p: getattr(p, name))


def _mask(name: str) -> FieldSpec:
    return FieldSpec(BOOL, lambda p: p.on_bars(getattr(p, name)))


def _member(attribute: str, params: tuple[str, ...]) -> Family:
    """1.0 on the bars of codes whose attribute (boards or exchanges) equals the parameter."""
    return Family(BOOL, params, lambda p, v: p.on_bars(np.broadcast_to(getattr(p, attribute) == v, p.traded.shape)))


def _in_index(panel: Panel, index: str) -> np.ndarray:
    """1.0 on the bars of the index's members that day, 0.0 on other bars, NaN outside its stored span."""
    member = panel.member(STORED_AS.get(index, index))
    return np.where(panel.traded & (member >= 0), member == 1, np.nan)


def _list_days(panel: Panel) -> np.ndarray:
    """Trading days listed on each bar, from securities.first_date; NaN without a bar or before it."""
    return np.where(panel.traded & (panel.list_days > 0), panel.list_days, np.nan)


FIELDS: dict[str, FieldSpec] = {
    **{name: _raw(name) for name in ("open", "high", "low", "close", "preclose", "volume", "amount")},
    **{f"adj_{name}": _adjusted(name) for name in ("open", "high", "low", "close", "preclose")},
    "factor": FieldSpec(NUM, lambda p: np.where(p.traded, p.factor, np.nan)),
    **{name: _limit(name) for name in ("limit_up_price", "limit_down_price")},
    **{name: _mask(name) for name in ("limit_up", "limit_down", "limit_locked", "limit_known", "st", "st_known")},
    "list_days": FieldSpec(NUM, _list_days),
}
FAMILIES: dict[str, Family] = {
    "board": _member("boards", BOARDS),
    "exchange": _member("exchanges", EXCHANGES),
    "member": Family(BOOL, INDEXES, _in_index),
}
DESCRIPTIVE: tuple[str, ...] = ("status", "listed", "suspended", "missing")
FUTURE: frozenset[str] = frozenset(
    "BACKSET BARSNEXT CONST CURRBARSCOUNT DRAWLINE FILTERX ISLASTBAR PEAK PEAKBARS REFX REFXV TOTALBARSCOUNT TROUGH "
    "TROUGHBARS XMA ZIG".split()
)


def _op(function: Callable[..., np.ndarray], roles: str, result: str = NUM) -> Signature:
    return Signature(function, tuple(roles.split()), result)


OPERATORS: dict[str, Signature] = {
    "REF": _op(ops.REF, "s n0", SAME),
    "MA": _op(ops.MA, "s n1"),
    "EMA": _op(ops.EMA, "s n1"),
    "SMA": _op(ops.SMA, "s n1 w"),
    "WMA": _op(ops.WMA, "s n1"),
    "SUM": _op(ops.SUM, "s n0"),
    "HHV": _op(ops.HHV, "s n0"),
    "LLV": _op(ops.LLV, "s n0"),
    "HHVBARS": _op(ops.HHVBARS, "s n0"),
    "LLVBARS": _op(ops.LLVBARS, "s n0"),
    "STD": _op(ops.STD, "s n2"),
    "STDP": _op(ops.STDP, "s n1"),
    "AVEDEV": _op(ops.AVEDEV, "s n1"),
    "SLOPE": _op(ops.SLOPE, "s n2"),
    "COUNT": _op(ops.COUNT, "c n0"),
    "EVERY": _op(ops.EVERY, "c n1", BOOL),
    "EXIST": _op(ops.EXIST, "c n1", BOOL),
    "TS_RANK": _op(ops.TS_RANK, "s n1"),
    "CORR": _op(ops.CORR, "s s n2"),
    "CROSS": _op(ops.CROSS, "s x", BOOL),
    "BARSLAST": _op(ops.BARSLAST, "c"),
    "BARSCOUNT": _op(ops.BARSCOUNT, "s"),
    "VALUEWHEN": _op(ops.VALUEWHEN, "c s", SAME),
    "CS_RANK": _op(ops.CS_RANK, "s"),
    "CS_PCT": _op(ops.CS_PCT, "s"),
    "CS_ZSCORE": _op(ops.CS_ZSCORE, "s"),
    "CS_DEMEAN": _op(ops.CS_DEMEAN, "s"),
    "GT": _op(ops.GT, "x x", BOOL),
    "GE": _op(ops.GE, "x x", BOOL),
    "LT": _op(ops.LT, "x x", BOOL),
    "LE": _op(ops.LE, "x x", BOOL),
    "EQ": _op(ops.EQ, "x x", BOOL),
    "AND": _op(ops.AND, "c c", BOOL),
    "OR": _op(ops.OR, "c c", BOOL),
    "NOT": _op(ops.NOT, "c", BOOL),
    "IF": _op(ops.IF, "c x x", SAME),
    "BETWEEN": _op(ops.BETWEEN, "x x x", BOOL),
    "MAX": _op(ops.MAX, "x x"),
    "MIN": _op(ops.MIN, "x x"),
    "ABS": _op(ops.ABS, "x"),
    "DIV": _op(ops.DIV, "x x"),
    "LN": _op(ops.LN, "x"),
    "ADD": _op(ops.ADD, "x x"),
    "SUB": _op(ops.SUB, "x x"),
    "MUL": _op(ops.MUL, "x x"),
}
