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
from wequant.expr import (
    COUNT,
    DESCRIPTIVE,
    FIELDS,
    FUTURE,
    GT,
    IF,
    MA,
    MAX,
    MAX_DEPTH,
    MAX_NODES,
    OPERATORS,
    REF,
    SMA,
    VALUEWHEN,
    ExprError,
    Field,
    LookAheadError,
    Op,
    from_form,
)
from wequant.expr.vocab import BOOL, NUM

COUNTS = [  # every count argument of every operator, with its minimum, as the vocabulary declares it
    (name, i, int(role[1])) for name, sig in OPERATORS.items() for i, role in enumerate(sig.roles) if role[0] == "n"
]
MAX_BARS = 2**62  # the largest count a node accepts

close, adj, volume, st = Field("close"), Field("adj_close"), Field("volume"), Field("st")
up, limit_up = close > REF(close, 1), Field("limit_up")


# ---------------------------------------------------------------- the vocabulary against wequant.ops


def test_operators_are_the_ops_functions_with_their_roles():
    assert set(OPERATORS) == set(ops.__all__)
    assert all(OPERATORS[name].function is getattr(ops, name) for name in ops.__all__)
    for sig in OPERATORS.values():
        assert len(sig.roles) == len(inspect.signature(sig.function).parameters)


def test_fields_and_families_are_pinned():
    """No future function or descriptive day status is in the vocabulary, and a family name is never a field."""
    assert not any("@" in name for name in FIELDS)
    names = set(FIELDS) | set(OPERATORS)
    assert not FUTURE & names and not set(DESCRIPTIVE) & names


def test_builders_carry_the_ops_signature_and_docstring():
    for name in ops.__all__:
        assert getattr(expr, name)(*arguments(name)) == Op(name, *arguments(name))
    assert SMA(adj, 6) == Op("SMA", adj, 6, 1) and SMA(adj, n=6, m=2) == Op("SMA", adj, 6, 2)


def test_importing_expr_loads_neither_pandas_nor_baostock():
    probe = "import sys, wequant.ops, wequant.expr; print('pandas' in sys.modules, 'baostock' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]


# ---------------------------------------------------------------- kinds


@pytest.mark.parametrize(
    ("e", "kind"),
    [
        (REF(limit_up, 1), BOOL),
        (IF(st, limit_up, None), BOOL),
        (IF(st, 1, 0), NUM),
        (MAX(limit_up, limit_up), NUM),
        (VALUEWHEN(limit_up, close), NUM),
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
        Op("BETWEEN", Op("SMA", adj, np.int32(5), np.int64(2)), np.float32(1.5), 2),
        Op("BETWEEN", Op("SMA", adj, 5, 2), 1.5, 2.0),
        '["BETWEEN",["SMA","adj_close",5,2],1.5,2.0]',
    ),
]


@pytest.mark.parametrize(("python", "op", "key"), KEYS, ids=[key for _, _, key in KEYS])
def test_canonical_keys(python, op, key):
    parsed = from_form(json.loads(key))
    assert python.key == op.key == parsed.key == key
    assert python == op == parsed and hash(python) == hash(op) == hash(parsed)


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
REJECTED = [  # one or two per error code; the code and the argument at fault are the contract, not the message
    ("REF(c, -1)", LookAheadError, 1, "negative_window"),
    ("REF(c, Lying(-1))", LookAheadError, 1, "negative_window"),
    ("Field('status')", LookAheadError, None, "descriptive_field"),
    ("Op('ZIG', c, 5)", LookAheadError, None, "future_function"),
    ("Field('ZIG')", LookAheadError, None, "future_function"),
    ("MA(from_form('ISLASTBAR'), 5)", LookAheadError, None, "future_function"),
    ("MA(c, Field('volume'))", ExprError, 1, "needs_integer"),
    ("MA(c, 2.0)", ExprError, 1, "needs_integer"),
    ("MA(c, True)", ExprError, 1, "needs_integer"),
    ("REF(c, np.timedelta64(20, 'ns'))", ExprError, 1, "needs_integer"),
    ("REF(c, np.timedelta64('NaT', 'ns'))", ExprError, 1, "needs_integer"),
    ("SMA(c, 3, 4)", ExprError, 2, "out_of_range"),
    ("SMA(c, 3, 0)", ExprError, 2, "out_of_range"),
    ("AND(c, Field('open'))", ExprError, 0, "needs_condition"),
    ("NOT(1.0)", ExprError, 0, "needs_condition"),
    ("MA(5.0, 3)", ExprError, 0, "needs_series"),
    ("GT(1, 2)", ExprError, None, "needs_series"),
    ("GT(c, math.nan)", ExprError, 1, "bad_constant"),
    ("GT(c, True)", ExprError, 1, "bad_constant"),
    ("GT(c, np.timedelta64(5, 'ns'))", ExprError, 1, "bad_constant"),
    ("GT(c, 10**400)", ExprError, 1, "bad_constant"),
    ("c + np.ones(3)", ExprError, 1, "bad_constant"),
    ("Field('traded')", ExprError, None, "unknown_field"),
    ("Field('board@nope')", ExprError, None, "unknown_field"),
    ("Field('@main')", ExprError, None, "unknown_field"),
    ("Field(1)", ExprError, None, "not_a_form"),
    ("from_form([1, 2])", ExprError, None, "not_a_form"),
    ("Op('NOPE', c)", ExprError, None, "unknown_operator"),
    ("Op('MA', c)", ExprError, None, "bad_arity"),
    ("MA(c)", ExprError, None, "bad_arity"),
]


@pytest.mark.parametrize(("text", "error", "arg", "code"), REJECTED, ids=[row[0] for row in REJECTED])
def test_rejected(text, error, arg, code):
    with pytest.raises(ExprError) as raised:
        eval(text, SCOPE)
    assert type(raised.value) is error and raised.value.arg == arg and raised.value.code == code


@pytest.mark.parametrize(
    ("form", "code", "path"),
    [
        (["GT", ["MA", "adj_close", 0], 10], "out_of_range", (1, 2)),
        (["GT", ["MA", "adj_close", 5], ["ABS", "nope"]], "unknown_field", (2, 1)),
        (["AND", ["GT", "close", 1], ["MA", "close", 5]], "needs_condition", (2,)),
        (["GT", "close", ["SMA", "close", 3, 4]], "out_of_range", (2, 3)),
        (["NOPE", "close"], "unknown_operator", ()),
    ],
)
def test_a_form_error_has_a_code_and_the_path_to_its_fault(form, code, path):
    with pytest.raises(ExprError) as raised:
        from_form(form)
    assert (raised.value.code, raised.value.path) == (code, path)


@pytest.mark.parametrize(("name", "i", "minimum"), COUNTS)
def test_every_negative_count_looks_ahead_and_every_short_one_is_refused(name, i, minimum):
    args = arguments(name)
    args[i] = -1
    with pytest.raises(LookAheadError) as raised:
        Op(name, *args)
    assert (raised.value.code, raised.value.arg) == ("negative_window", i)
    for n in (minimum - 1, MAX_BARS + 1):
        if n >= 0:
            args[i] = n
            with pytest.raises(ExprError) as raised:
                Op(name, *args)
            assert (type(raised.value), raised.value.code, raised.value.arg) == (ExprError, "out_of_range", i)
    args[i] = np.int64(minimum)
    assert Op(name, *args).args[i] == minimum
    # wequant.ops agrees with the vocabulary: it refuses minimum - 1 and computes minimum and MAX_BARS
    grid = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    values = [grid if role in ("x", "s", "c") else 1 if role == "w" else minimum for role in OPERATORS[name].roles]
    function = getattr(ops, name)
    with pytest.raises(ValueError):
        function(*[minimum - 1 if k == i else v for k, v in enumerate(values)])
    assert function(*values).shape == grid.shape
    assert function(*[MAX_BARS if k == i else v for k, v in enumerate(values)]).shape == grid.shape


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
    assert raised.value.arg is None and raised.value.code == "too_large"


def test_depth_limit():
    x = close
    for _ in range(MAX_DEPTH - 1):
        x = x + 1.0
    assert (x.depth, x.size) == (MAX_DEPTH, MAX_DEPTH)
    assert repr(x).startswith("ADD(ADD(") and from_form(json.loads(x.key)) == x
    with pytest.raises(ExprError, match="deeper than 256 levels") as raised:
        x + 1.0
    assert raised.value.arg is None and raised.value.code == "too_deep"
    with pytest.raises(ExprError, match="deeper than 256 levels"):
        from_form(["ADD", x.form, 1.0])


def test_a_form_far_too_deep_is_refused_before_python_runs_out_of_stack():
    form = "close"
    for _ in range(2000):
        form = ["ABS", form]
    with pytest.raises(ExprError, match="deeper than 256 levels") as raised:
        from_form(form)
    assert raised.value.arg is None and raised.value.code == "too_deep" and raised.value.path == (1,) * MAX_DEPTH


def test_checks_run_in_order_and_the_first_failure_wins():
    with pytest.raises(ExprError) as raised:
        COUNT(adj, -1)
    assert type(raised.value) is ExprError and raised.value.arg == 0
    with pytest.raises(LookAheadError):
        Op("ZIG", "not even an expression")
    with pytest.raises(ExprError, match="takes 2 arguments"):
        Op("AND", 1.0)
