"""Random well-typed trees: canonical forms round-trip, and evaluation equals a naive evaluator bit for bit."""

import json

import numpy as np
from hypothesis import assume, given
from hypothesis import strategies as st

from wequant.expr import (
    BOOL,
    FAMILIES,
    FIELDS,
    OPERATORS,
    Expr,
    ExprError,
    Field,
    Op,
    evaluate,
    evaluate_all,
    from_form,
)
from wequant.expr.vocab import SAME

DEPTH = 4
CONSTANTS = [-1.5, 0.0, 2.0, 10.0, None]
FIELD_NAMES = sorted([*FIELDS, *(f"{f}@{p}" for f, spec in FAMILIES.items() for p in spec.params)])
CONDITION_FIELDS = [name for name in FIELD_NAMES if Field(name).kind == BOOL]
CONDITION_OPERATORS = sorted(name for name, sig in OPERATORS.items() if sig.result in (BOOL, SAME))


def trees(depth: int, condition: bool = False) -> st.SearchStrategy:
    """A field, or an operator call of at most depth levels; a bool one when condition is set."""
    fields = st.sampled_from(CONDITION_FIELDS if condition else FIELD_NAMES).map(Field)
    return fields if depth == 1 else st.one_of(fields, calls(depth, condition))


@st.composite
def calls(draw, depth: int = DEPTH, condition: bool = False) -> Op:
    """An operator call of at most depth levels with drawn arguments; a bool one when condition is set."""
    name = draw(st.sampled_from(CONDITION_OPERATORS if condition else sorted(OPERATORS)))
    args = []
    for role in OPERATORS[name].roles:
        args.append(draw(argument(role, depth - 1, args)))
    try:
        tree = Op(name, *args)
    except ExprError:
        assume(False)
    assume(not condition or tree.kind == BOOL)
    return tree


def argument(role: str, depth: int, before: list) -> st.SearchStrategy:
    if role == "x":
        return st.one_of(trees(depth), st.sampled_from(CONSTANTS))
    if role in ("s", "c"):
        return trees(depth, condition=role == "c")
    if role == "w":
        return st.integers(1, before[-1])
    return st.integers(int(role[1]), int(role[1]) + 3)


def naive(node, panel):
    """node evaluated by direct OPERATORS calls: no memo, no cache."""
    if not isinstance(node, Expr):
        return np.nan if node is None else node
    if isinstance(node, Op):
        return OPERATORS[node.name].function(*[naive(a, panel) for a in node.args])
    if node.name in FIELDS:
        return FIELDS[node.name].load(panel)
    family, _, param = node.name.partition("@")
    return FAMILIES[family].load(panel, param)


def subtrees(tree: Expr) -> list[Expr]:
    nodes, stack = [], [tree]
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, Op):
            stack.extend(a for a in node.args if isinstance(a, Expr))
    return nodes


def identical(a, b) -> bool:
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


@given(tree=calls())
def test_the_canonical_form_round_trips(tree):
    parsed = from_form(json.loads(tree.key))
    assert parsed == tree and parsed.key == tree.key and (parsed.kind, parsed.size) == (tree.kind, tree.size)
    assert tree.key == json.dumps(tree.form, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@given(tree=calls(), data=st.data())
def test_evaluate_equals_the_naive_evaluator(panel, free_panel, tree, data):
    on = data.draw(st.sampled_from([panel, free_panel]))
    assert identical(evaluate(tree, on), naive(tree, on))


@given(tree=calls(), data=st.data())
def test_evaluate_all_equals_separate_evaluations(panel, tree, data):
    nodes = subtrees(tree)
    exprs = {"tree": tree, "a": data.draw(st.sampled_from(nodes)), "b": data.draw(st.sampled_from(nodes))}
    together = evaluate_all(exprs, panel)
    assert all(identical(together[name], evaluate(e, panel)) for name, e in exprs.items())
