"""Evaluation on synthetic stores: goldens, shared subexpressions, the cache, no look-ahead, FREE cells and parity."""

import json
import sys
from collections import Counter

import numpy as np
import pytest

from tests.expr.conftest import SH, SZ, arguments
from wequant import ops
from wequant.expr import (
    CS_RANK,
    FAMILIES,
    FIELDS,
    GE,
    HHV,
    IF,
    LLV,
    MA,
    MAX_DEPTH,
    OPERATORS,
    REF,
    SMA,
    Cache,
    Field,
    Op,
    evaluate,
    evaluate_all,
    from_form,
    panel_key,
)

NAN = np.nan
close, adj, st, limit_up, limit_up_price = (
    Field(n) for n in ("close", "adj_close", "st", "limit_up", "limit_up_price")
)
up = close > REF(close, 1)
FAMILY_NAMES = [f"{family}@{param}" for family, spec in FAMILIES.items() for param in spec.params]
GOLDENS = [  # expression, 000001.SZ, 600000.SH, relative tolerance
    (
        adj / REF(adj, 1) - 1,  # SZ D3 is 22.55 / 19.5 - 1; the raw close would give 20.5 / 19.5 - 1
        [NAN, -0.05, 0.0263157894736842, 0.1564102564102565, -0.0243902439024390, 0.05],
        [NAN, 0.05, 0.1, NAN, -0.0476190476190476, 0.1],
        1e-12,
    ),
    (CS_RANK(IF(st, None, adj)), [2, 2, 2, 1, NAN, NAN], [1, 1, 1, NAN, 1, 1], 0),  # ST leaves the ranking
]
GOLDEN_IDS = [repr(e) for e, *_ in GOLDENS]
FIELD_GOLDENS = {  # 000001.SZ, 600000.SH
    "limit_up": ([0, 0, 0, 0, 0, 1], [0, 0, 1, NAN, 0, 1]),
    "st": ([0, 0, 0, 0, 1, 1], [0, 0, 0, NAN, 0, 0]),
    "factor": ([1, 1, 1, 1.1, 1.1, 1.1], [1, 1, 1, NAN, 1, 1]),
    "exchange@SH": ([0, 0, 0, 0, 0, 0], [1, 1, 1, NAN, 1, 1]),
    # trading days since securities.first_date (D0 for both), that day counted as 1; no value without a bar
    "list_days": ([1, 2, 3, 4, 5, 6], [1, 2, 3, NAN, 5, 6]),
}
PANEL_GRIDS = {  # one field per loader kind, and the panel array behind it
    "close": lambda panel: panel.raw("close"),
    "adj_close": lambda panel: panel.adjusted("close"),
    "limit_up_price": lambda panel: panel.limit_up_price,
}


def expect(grid, first, second, rtol=0.0):
    """The two columns of grid are first and second, NaN positions exactly."""
    np.testing.assert_allclose(grid, np.column_stack([first, second]), rtol=rtol, atol=0)


def identical(a, b) -> bool:
    """The same shape and the same bits in every cell."""
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


@pytest.fixture
def calls(monkeypatch):
    """Calls of each operator during the test, counted by wrappers patched into OPERATORS."""
    counts = Counter()

    def counting(name, function):
        def call(*args):
            counts[name] += 1
            return function(*args)

        return call

    for name, sig in list(OPERATORS.items()):
        monkeypatch.setitem(OPERATORS, name, sig._replace(function=counting(name, sig.function)))
    return counts


# ---------------------------------------------------------------- panel facts and goldens


@pytest.mark.parametrize(("e", "sz", "sh", "rtol"), GOLDENS, ids=GOLDEN_IDS)
def test_goldens(panel, e, sz, sh, rtol):
    expect(evaluate(e, panel), sz, sh, rtol)


@pytest.mark.parametrize(("name", "sz", "sh"), [(name, *columns) for name, columns in FIELD_GOLDENS.items()])
def test_field_goldens(panel, name, sz, sh):
    expect(evaluate(Field(name), panel), sz, sh)


# ---------------------------------------------------------------- repeated subexpressions


def written(form) -> Counter:
    """Operator names as often as they appear in the expanded form."""
    counts, stack = Counter(), [form]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            counts[node[0]] += 1
            stack.extend(node[1:])
    return counts


def test_kdj_computes_each_distinct_node_once(panel, calls):
    l2 = LLV(Field("adj_low"), 2)
    rsv = (adj - l2) / (HHV(Field("adj_high"), 2) - l2) * 100
    k = SMA(rsv, 3, 1)
    e = 3 * k - 2 * SMA(k, 3, 1) < 0
    assert written(e.form) == {"LLV": 4, "HHV": 2, "SMA": 3, "SUB": 5, "DIV": 2, "MUL": 4, "LT": 1}
    result = evaluate(e, panel)
    assert calls == {"LLV": 1, "HHV": 1, "SMA": 2, "SUB": 3, "DIV": 1, "MUL": 3, "LT": 1}
    low, high, adj_close = (panel.adjusted(field) for field in ("low", "high", "close"))
    lowest = ops.LLV(low, 2)
    rsv = ops.DIV(ops.SUB(adj_close, lowest), ops.SUB(ops.HHV(high, 2), lowest))
    k = ops.SMA(ops.MUL(rsv, 100.0), 3, 1)
    assert identical(result, ops.LT(ops.SUB(ops.MUL(3.0, k), ops.MUL(2.0, ops.SMA(k, 3, 1))), 0.0))


def test_roots_share_their_subexpressions(panel, calls):
    results = evaluate_all({"a": MA(close, 2) > 10, "b": MA(close, 2) < 20, "c": MA(close, 2) > 10}, panel)
    assert list(results) == ["a", "b", "c"] and results["a"] is results["c"]
    assert calls == {"MA": 1, "GT": 1, "LT": 1}


def test_a_cached_root_serves_the_roots_that_contain_it(panel, calls):
    cache = Cache()
    evaluate(MA(close, 2), panel, cache)
    evaluate_all({"a": MA(close, 2), "b": MA(close, 2) > 10}, panel, cache)
    assert calls == {"MA": 1, "GT": 1} and (cache.hits, cache.misses) == (1, 2)


def test_a_seeded_node_is_taken_from_the_seed_and_never_computed(panel, calls):
    ma = MA(close, 2)
    plain = evaluate(ma > 10, panel)
    calls.clear()
    seeded = evaluate_all({"a": ma > 10, "b": ma - 1}, panel, seed={ma.key: evaluate(ma, panel)})
    assert calls == {"MA": 1, "GT": 1, "SUB": 1}  # the one MA call made the seed
    assert identical(seeded["a"], plain)
    assert evaluate(ma, panel, seed={ma.key: plain}) is plain  # a seeded root is the seed's grid


# ---------------------------------------------------------------- cache


def test_the_cache_hits_a_repeat_and_misses_any_other_panel(panel, load, two_versions):
    cache, e = Cache(), MA(close, 2)
    first = evaluate(e, panel, cache)
    assert evaluate(e, panel, cache) is first and (cache.misses, cache.hits) == (1, 1)
    assert not first.flags.writeable
    assert evaluate(from_form(json.loads(e.key)), panel, cache) is first and cache.hits == 2
    assert identical(evaluate(e, load(codes=[SH]), cache)[:, 0], first[:, 1]) and cache.misses == 2
    evaluate(e, two_versions[1], cache)
    assert cache.misses == 3
    assert evaluate(e, load(end="2024-01-05"), cache).shape == (4, 2) and cache.misses == 4
    assert evaluate(e, load(start="2024-01-03"), cache).shape == (5, 2) and cache.misses == 5
    assert identical(evaluate(e, load(codes=[SH, SZ]), cache), first[:, ::-1]) and cache.misses == 6
    assert (cache.hits, cache.nbytes) == (2, 96 + 48 + 96 + 64 + 80 + 96)


def test_panel_key(panel, load):
    ident = f"{panel.key}-{panel.rules_key}"
    assert panel_key(panel) == f"{ident}-2024-01-02-2024-01-09-dc54b75db825"
    assert panel_key(load(end="2024-01-05")) == f"{ident}-2024-01-02-2024-01-05-dc54b75db825"
    assert panel_key(load(codes=[SH])) == f"{ident}-2024-01-02-2024-01-09-56dab30ebda9"
    assert panel_key(load(codes=[SH, SZ])) == f"{ident}-2024-01-02-2024-01-09-a72db9b7ec43"


def test_the_cache_evicts_the_least_recently_used():
    cache = Cache(max_bytes=200)
    a, b, c = np.zeros(12), np.zeros(12), np.zeros(12)
    cache.put("a", a)
    cache.put("b", b)
    assert cache.get("a") is a
    cache.put("c", c)
    assert cache.nbytes == 192 and cache.get("b") is None and (cache.hits, cache.misses) == (1, 1)
    assert cache.get("a") is a and cache.get("c") is c
    cache.put("c", c)  # a repeat replaces the grid: its bytes count once, so nothing else has to leave
    assert cache.nbytes == 192 and cache.get("a") is a and cache.get("c") is c
    small, grid = Cache(max_bytes=50), np.zeros(12)
    small.put("x", grid)  # the newest grid stays even when it alone exceeds max_bytes
    assert small.get("x") is grid and small.nbytes == 96


# ---------------------------------------------------------------- no look-ahead


EVERYTHING = {
    **{f"op {name}": Op(name, *arguments(name)) for name in OPERATORS},
    **{name: Field(name) for name in [*FIELDS, *FAMILY_NAMES]},
}


def test_later_data_never_moves_an_earlier_row(two_versions):
    v1, v2 = (evaluate_all(EVERYTHING, panel) for panel in two_versions)
    assert all(identical(v1[name][:4], v2[name][:4]) for name in EVERYTHING)
    assert any(not identical(v1[f"op {name}"][4:], v2[f"op {name}"][4:]) for name in OPERATORS)


def test_a_shorter_panel_gives_the_same_rows(panel, load):
    full, short = (evaluate_all(EVERYTHING, p) for p in (panel, load(end="2024-01-05")))
    assert all(identical(short[name], full[name][:4]) for name in EVERYTHING)


# ---------------------------------------------------------------- FREE cells


def test_free_limits_are_infinite_in_the_bare_field_and_no_value_elsewhere(free_panel):
    none = [NAN] * 6
    assert identical(evaluate(limit_up_price, free_panel), free_panel.limit_up_price)
    assert np.isposinf(evaluate(limit_up_price, free_panel)[1:, 1]).all()
    expect(evaluate(Field("limit_known"), free_panel), [0, 1, 1, 1, 1, 1], [NAN, 1, 1, 1, 1, 1])
    expect(evaluate(GE(close, limit_up_price), free_panel), [NAN, 0, 0, 0, 0, 0], none)
    expect(evaluate(limit_up_price + 0.0, free_panel), [NAN, 22.0, 20.9, 21.45, 22.55, 22.0], none)
    expect(evaluate(CS_RANK(limit_up_price), free_panel), [NAN, 1, 1, 1, 1, 1], none)
    expect(evaluate(limit_up, free_panel), [0, 0, 0, 0, 0, 0], [NAN, 0, 0, 0, 0, 0])


# ---------------------------------------------------------------- parity with direct calls


@pytest.mark.parametrize("name", list(PANEL_GRIDS))
def test_a_field_is_the_panel_grid_itself(panel, name):
    assert evaluate(Field(name), panel) is PANEL_GRIDS[name](panel)


def test_an_operator_gets_and_gives_its_grids_uncopied(panel, monkeypatch):
    seen = []

    def ma(x, n):
        seen.extend([x, ops.MA(x, n)])
        return seen[-1]

    monkeypatch.setitem(OPERATORS, "MA", OPERATORS["MA"]._replace(function=ma))
    result = evaluate(MA(close, 2), panel)
    assert seen[0] is panel.raw("close") and result is seen[1]


def test_the_deepest_tree_evaluates_under_the_default_recursion_limit(panel):
    assert sys.getrecursionlimit() <= 1000
    x, expected = close, panel.raw("close")
    for _ in range(MAX_DEPTH - 1):
        x, expected = x + 1.0, ops.ADD(expected, 1.0)
    result = evaluate(x, panel)
    assert identical(result, expected)
    np.testing.assert_allclose(result, panel.raw("close") + 255.0, rtol=1e-12)
