"""Expression trees: the vocabulary pinned against wequant.ops, kinds, canonical keys and every construction rule."""

import inspect
import json
import math
import subprocess
import sys

import numpy as np
import pytest

from tests.expr.conftest import arguments
from wequant import expr, ops
from wequant.data.codes import BOARDS, EXCHANGES
from wequant.expr import (
    AND,
    COUNT,
    DESCRIPTIVE,
    EVERY,
    FAMILIES,
    FIELDS,
    FUTURE,
    GT,
    IF,
    MA,
    MAX,
    MAX_DEPTH,
    MAX_NODES,
    NOT,
    OPERATORS,
    REF,
    SMA,
    STD,
    SUM,
    VALUEWHEN,
    ExprError,
    Field,
    LookAheadError,
    Op,
    from_form,
)
from wequant.expr.vocab import BOOL, NUM, SAME

SIGNATURES = {  # every operator's roles and result kind
    "REF": (("s", "n0"), SAME),
    "MA": (("s", "n1"), NUM),
    "EMA": (("s", "n1"), NUM),
    "WMA": (("s", "n1"), NUM),
    "STDP": (("s", "n1"), NUM),
    "AVEDEV": (("s", "n1"), NUM),
    "TS_RANK": (("s", "n1"), NUM),
    "SUM": (("s", "n0"), NUM),
    "HHV": (("s", "n0"), NUM),
    "LLV": (("s", "n0"), NUM),
    "HHVBARS": (("s", "n0"), NUM),
    "LLVBARS": (("s", "n0"), NUM),
    "STD": (("s", "n2"), NUM),
    "SLOPE": (("s", "n2"), NUM),
    "SMA": (("s", "n1", "w"), NUM),
    "CORR": (("s", "s", "n2"), NUM),
    "COUNT": (("c", "n0"), NUM),
    "EVERY": (("c", "n1"), BOOL),
    "EXIST": (("c", "n1"), BOOL),
    "CROSS": (("s", "x"), BOOL),
    "BARSLAST": (("c",), NUM),
    "BARSCOUNT": (("s",), NUM),
    "VALUEWHEN": (("c", "s"), SAME),
    "CS_RANK": (("s",), NUM),
    "CS_PCT": (("s",), NUM),
    "CS_ZSCORE": (("s",), NUM),
    "CS_DEMEAN": (("s",), NUM),
    "GT": (("x", "x"), BOOL),
    "GE": (("x", "x"), BOOL),
    "LT": (("x", "x"), BOOL),
    "LE": (("x", "x"), BOOL),
    "EQ": (("x", "x"), BOOL),
    "AND": (("c", "c"), BOOL),
    "OR": (("c", "c"), BOOL),
    "NOT": (("c",), BOOL),
    "IF": (("c", "x", "x"), SAME),
    "BETWEEN": (("x", "x", "x"), BOOL),
    "MAX": (("x", "x"), NUM),
    "MIN": (("x", "x"), NUM),
    "DIV": (("x", "x"), NUM),
    "ADD": (("x", "x"), NUM),
    "SUB": (("x", "x"), NUM),
    "MUL": (("x", "x"), NUM),
    "ABS": (("x",), NUM),
    "LN": (("x",), NUM),
}
FIELD_KINDS = {  # every field's kind
    **dict.fromkeys(["open", "high", "low", "close", "preclose", "volume", "amount"], NUM),
    **dict.fromkeys(["adj_open", "adj_high", "adj_low", "adj_close", "adj_preclose"], NUM),
    "factor": NUM,
    "limit_up_price": NUM,
    "limit_down_price": NUM,
    **dict.fromkeys(["limit_up", "limit_down", "limit_locked", "limit_known", "st", "st_known"], BOOL),
}
COUNTS = [
    (name, i, int(role[1])) for name, (roles, _) in SIGNATURES.items() for i, role in enumerate(roles) if role[0] == "n"
]
MAX_BARS = 2**62  # the largest count a node accepts

close, adj, volume, st = Field("close"), Field("adj_close"), Field("volume"), Field("st")
up, limit_up, bj = close > REF(close, 1), Field("limit_up"), Field("exchange@BJ")


# ---------------------------------------------------------------- the vocabulary against wequant.ops


def test_operators_are_the_ops_functions_with_their_roles():
    assert set(OPERATORS) == set(ops.__all__) and len(OPERATORS) == 45
    assert all(OPERATORS[name].function is getattr(ops, name) for name in ops.__all__)
    assert {name: (sig.roles, sig.result) for name, sig in OPERATORS.items()} == SIGNATURES
    for sig in OPERATORS.values():
        assert len(sig.roles) == len(inspect.signature(sig.function).parameters)


def test_fields_and_families_are_pinned():
    assert {name: spec.kind for name, spec in FIELDS.items()} == FIELD_KINDS and len(FIELDS) == 21
    assert {name: (fam.params, fam.kind) for name, fam in FAMILIES.items()} == {
        "board": (BOARDS, BOOL),
        "exchange": (EXCHANGES, BOOL),
    }
    assert not any("@" in name for name in FIELDS)
    names = set(FIELDS) | set(OPERATORS)
    assert not FUTURE & names and not set(DESCRIPTIVE) & names
    assert DESCRIPTIVE == ("status", "listed", "suspended", "missing")
    assert FUTURE == frozenset(
        {
            *("BACKSET", "BARSNEXT", "CONST", "CURRBARSCOUNT", "DRAWLINE", "FILTERX", "ISLASTBAR", "PEAK"),
            *("PEAKBARS", "REFX", "REFXV", "TOTALBARSCOUNT", "TROUGH", "TROUGHBARS", "XMA", "ZIG"),
        }
    )


@pytest.mark.parametrize(("name", "i", "minimum"), COUNTS)
def test_count_minimums_are_those_of_ops(name, i, minimum):
    grid = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    roles = OPERATORS[name].roles
    values = [grid if role in ("x", "s", "c") else 1 if role == "w" else minimum for role in roles]
    with pytest.raises(ValueError):
        getattr(ops, name)(*[minimum - 1 if k == i else v for k, v in enumerate(values)])
    assert getattr(ops, name)(*values).shape == grid.shape
    assert getattr(ops, name)(*[MAX_BARS if k == i else v for k, v in enumerate(values)]).shape == grid.shape


def test_builders_carry_the_ops_signature_and_docstring():
    for name in ops.__all__:
        builder, function = getattr(expr, name), getattr(ops, name)
        assert inspect.signature(builder) == inspect.signature(function) and builder.__doc__ == function.__doc__
        assert builder.__name__ == name and builder(*arguments(name)) == Op(name, *arguments(name))
    assert SMA(adj, 6) == Op("SMA", adj, 6, 1) and SMA(adj, n=6, m=2) == Op("SMA", adj, 6, 2)


def test_importing_expr_loads_neither_pandas_nor_baostock():
    probe = "import sys, wequant.expr; print('pandas' in sys.modules, 'baostock' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]


# ---------------------------------------------------------------- kinds


@pytest.mark.parametrize(
    ("e", "kind"),
    [
        (MA(close, 2), NUM),
        (up, BOOL),
        (REF(limit_up, 1), BOOL),
        (IF(st, limit_up, None), BOOL),
        (IF(st, 1, 0), NUM),
        (EVERY(up, 2), BOOL),
        (MAX(limit_up, limit_up), NUM),
        (VALUEWHEN(limit_up, close), NUM),
        (bj, BOOL),
        (NOT(bj), BOOL),
    ],
    ids=repr,
)
def test_kinds(e, kind):
    assert e.kind == kind


# ---------------------------------------------------------------- canonical keys


KEYS = [
    (
        (close > MA(close, 20)) & ~st,
        Op("AND", Op("GT", close, Op("MA", close, 20)), Op("NOT", st)),
        '["AND",["GT","close",["MA","close",20]],["NOT","st"]]',
    ),
    (SMA(adj, 3), Op("SMA", adj, 3, 1), '["SMA","adj_close",3,1]'),
    (2 * volume, Op("MUL", 2.0, volume), '["MUL",2.0,"volume"]'),
    (np.float64(2.0) * volume, Op("MUL", 2, volume), '["MUL",2.0,"volume"]'),
    (-close, Op("SUB", 0.0, close), '["SUB",0.0,"close"]'),
    (10 < close, Op("GT", close, 10), '["GT","close",10.0]'),
    (GT(close, -0.0), Op("GT", close, 0.0), '["GT","close",0.0]'),
    (GT(close, 1e20), Op("GT", close, 1e20), '["GT","close",1e+20]'),
    (IF(st, None, adj), Op("IF", st, None, adj), '["IF","st",null,"adj_close"]'),
    (
        adj / REF(adj, 20) - 1,
        Op("SUB", Op("DIV", adj, Op("REF", adj, 20)), 1.0),
        '["SUB",["DIV","adj_close",["REF","adj_close",20]],1.0]',
    ),
    (
        ~bj & Field("board@main"),
        Op("AND", Op("NOT", bj), Field("board@main")),
        '["AND",["NOT","exchange@BJ"],"board@main"]',
    ),
]


@pytest.mark.parametrize(("python", "op", "key"), KEYS, ids=[key for _, _, key in KEYS])
def test_canonical_keys(python, op, key):
    parsed = from_form(json.loads(key))
    assert python.key == op.key == parsed.key == key
    assert python == op == parsed and hash(python) == hash(op) == hash(parsed)
    assert key == json.dumps(python.form, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def test_repr_and_form():
    e = (close > MA(close, 20)) & ~st
    assert repr(e) == "AND(GT(close, MA(close, 20)), NOT(st))" and repr(bj) == "exchange@BJ"
    assert repr(IF(st, None, adj)) == "IF(st, None, adj_close)" and repr(GT(close, 10)) == "GT(close, 10.0)"
    form = e.form
    form[0] = "OR"
    assert e.form[0] == "AND" and e.form is not e.form


def test_python_operators():
    assert 2 - close == Op("SUB", 2.0, close) and 2 / close == Op("DIV", 2.0, close)
    assert 2 + close == Op("ADD", 2.0, close) and close * adj == Op("MUL", close, adj)
    assert abs(close) == Op("ABS", close) and (close >= 1) == Op("GE", close, 1.0)
    assert (close <= adj) == Op("LE", close, adj) and (up | st) == Op("OR", up, st)
    assert close != Field("open") and close == Field("close") and (close == "close") is False
    assert len({close, Field("close"), adj}) == 2
    with pytest.raises(TypeError):
        True & up
    with pytest.raises(AttributeError):
        close.name = "open"
    with pytest.raises(AttributeError):
        del close.name
    assert close.name == "close"


# ---------------------------------------------------------------- rejections


class Lying(int):
    """An int whose every comparison says False."""

    def __lt__(self, other: object) -> bool:
        return False

    __le__ = __gt__ = __ge__ = __lt__


SCOPE = {**vars(expr), "c": adj, "np": np, "math": math, "json": json, "Lying": Lying}
REJECTED = [
    ("REF(c, -1)", LookAheadError, 1, "would read bars after day t"),
    ("from_form(['REF', 'close', -2])", LookAheadError, 1, "would read bars after day t"),
    ("SUM(c, -5)", LookAheadError, 1, "SUM with -5 bars"),
    ("REF(c, Lying(-1))", LookAheadError, 1, "REF with -1 bars"),
    *[(f"Field({name!r})", LookAheadError, None, "descriptive") for name in DESCRIPTIVE],
    ("Op('ZIG', c, 5)", LookAheadError, None, "future function"),
    ("from_form(['ZIG', 'close', 5])", LookAheadError, None, "future function"),
    *[(f"Op({name!r}, c)", LookAheadError, None, "future function") for name in sorted(FUTURE)],
    *[(f"Field({name!r})", LookAheadError, None, "future function") for name in sorted(FUTURE)],
    ("MA(from_form('ISLASTBAR'), 5)", LookAheadError, None, "future function"),
    ("MA(c, Field('volume'))", ExprError, 1, "must be an integer constant"),
    ("MA(c, 2.0)", ExprError, 1, "must be an integer constant"),
    ("MA(c, True)", ExprError, 1, "must be an integer constant"),
    ("from_form(['MA', 'close', 20.0])", ExprError, 1, "must be an integer constant"),
    ("REF(c, np.timedelta64(20, 'ns'))", ExprError, 1, "must be an integer constant"),
    ("REF(c, np.timedelta64('NaT', 'ns'))", ExprError, 1, "must be an integer constant"),
    ("REF(c, 2**62 + 1)", ExprError, 1, "at most 4611686018427387904"),
    ("from_form(['MA', 'close', 2**63])", ExprError, 1, "at most"),
    ("MA(c, 0)", ExprError, 1, "at least 1"),
    ("STD(c, 1)", ExprError, 1, "at least 2"),
    ("SMA(c, 3, 4)", ExprError, 2, "from 1 to 3"),
    ("SMA(c, 3, 0)", ExprError, 2, "from 1 to 3"),
    ("AND(c, Field('open'))", ExprError, 0, "must be a condition"),
    ("~c", ExprError, 0, "must be a condition"),
    ("COUNT(c, 5)", ExprError, 0, "must be a condition"),
    ("NOT(1.0)", ExprError, 0, "must be a condition"),
    ("MA(5.0, 3)", ExprError, 0, "must be a series"),
    ("GT(1, 2)", ExprError, None, "needs at least one series argument"),
    ("GT(c, math.nan)", ExprError, 1, "finite number"),
    ("GT(c, math.inf)", ExprError, 1, "finite number"),
    ("GT(c, True)", ExprError, 1, "finite number"),
    ("GT(c, np.timedelta64(5, 'ns'))", ExprError, 1, "finite number"),
    ("GT(c, 10**400)", ExprError, 1, "finite number"),
    ("from_form(json.loads('[\"GT\",\"close\",1' + '0' * 400 + ']'))", ExprError, 1, "finite number"),
    ("c + np.ones(3)", ExprError, 1, "finite number"),
    ("GT(c, '1')", ExprError, 1, "finite number"),
    *[(f"Field({name!r})", ExprError, None, "unknown field") for name in ("traded", "nope", "board@nope")],
    *[(f"Field({name!r})", ExprError, None, "unknown field") for name in ("sector@bank", "board@", "@main")],
    ("Field(1)", ExprError, None, "string"),
    ("Op('NOPE', c)", ExprError, None, "unknown operator"),
    ("Op('MA', c)", ExprError, None, "MA takes 2 arguments, not 1"),
    ("Op('MA', c, 1, 2)", ExprError, None, "MA takes 2 arguments, not 3"),
    ("MA(c)", ExprError, None, "MA: missing a required argument"),
    ("MA(c, 1, 2)", ExprError, None, "MA: too many positional arguments"),
    ("SMA(c, n=3, q=1)", ExprError, None, "SMA: got an unexpected keyword argument"),
    ("from_form({'op': 1})", ExprError, None, "not an expression form"),
    ("from_form([])", ExprError, None, "not an expression form"),
    ("from_form([1, 2])", ExprError, None, "not an expression form"),
    ("from_form(['MA', ['MA', 'close', True], 2])", ExprError, 1, "must be an integer constant"),
]


@pytest.mark.parametrize(("text", "error", "arg", "message"), REJECTED, ids=[row[0] for row in REJECTED])
def test_rejected(text, error, arg, message):
    with pytest.raises(ExprError, match=message) as raised:
        eval(text, SCOPE)
    assert type(raised.value) is error and raised.value.arg == arg


@pytest.mark.parametrize(("name", "i", "minimum"), COUNTS)
def test_every_negative_count_looks_ahead_and_every_short_one_is_refused(name, i, minimum):
    args = arguments(name)
    args[i] = -1
    with pytest.raises(LookAheadError) as raised:
        Op(name, *args)
    assert raised.value.arg == i
    args[i] = minimum - 1
    if minimum > 0:
        with pytest.raises(ExprError) as raised:
            Op(name, *args)
        assert type(raised.value) is ExprError and raised.value.arg == i
    args[i] = MAX_BARS + 1
    with pytest.raises(ExprError, match="at most") as raised:
        Op(name, *args)
    assert raised.value.arg == i
    args[i] = np.int64(minimum)
    assert Op(name, *args).args[i] == minimum


def test_constants_and_counts_are_stored_canonically():
    e = Op("SMA", adj, np.int32(5), np.int64(2))
    assert e.args[1:] == (5, 2) and all(type(a) is int for a in e.args[1:])
    assert all(type(a) is float for a in Op("BETWEEN", close, np.float32(1.5), 2).args[1:])
    assert GT(close, -0.0).args[1] == 0.0 and math.copysign(1.0, GT(close, -0.0).args[1]) == 1.0


def test_truth_value_is_refused():
    with pytest.raises(TypeError):
        bool(adj > 1)
    with pytest.raises(TypeError):
        0 < adj < 1
    with pytest.raises(TypeError):
        (adj > 1) and (adj < 2)


def test_size_limit():
    x = close
    for _ in range(12):
        x = x + x
    assert (x.size, x.depth) == (8191, 13) and 2 * x.size + 1 > MAX_NODES
    with pytest.raises(ExprError, match="more than 10000 nodes") as raised:
        x + x
    assert raised.value.arg is None


def test_depth_limit():
    x = close
    for _ in range(MAX_DEPTH - 1):
        x = x + 1.0
    assert (x.depth, x.size) == (MAX_DEPTH, MAX_DEPTH)
    assert repr(x).startswith("ADD(ADD(") and from_form(json.loads(x.key)) == x
    with pytest.raises(ExprError, match="deeper than 256 levels") as raised:
        x + 1.0
    assert raised.value.arg is None
    with pytest.raises(ExprError, match="deeper than 256 levels"):
        from_form(["ADD", x.form, 1.0])


def test_a_form_far_too_deep_is_refused_before_python_runs_out_of_stack():
    form = "close"
    for _ in range(2000):
        form = ["ABS", form]
    with pytest.raises(ExprError, match="deeper than 256 levels") as raised:
        from_form(form)
    assert raised.value.arg is None


def test_checks_run_in_order_and_the_first_failure_wins():
    with pytest.raises(ExprError) as raised:
        COUNT(adj, -1)
    assert type(raised.value) is ExprError and raised.value.arg == 0
    with pytest.raises(LookAheadError):
        Op("ZIG", "not even an expression")
    with pytest.raises(ExprError, match="takes 2 arguments"):
        Op("AND", 1.0)
    assert AND(up, st).kind == BOOL and SUM(up, 0).kind == NUM and STD(adj, 2).kind == NUM
