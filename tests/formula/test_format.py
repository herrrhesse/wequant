"""S4.1: a tree written as formula text compiles back to the same tree."""

import pytest
from hypothesis import assume, given, settings

from tests.expr.test_properties import DEPTH, trees
from tests.formula.test_compile import SAMPLES
from wequant.expr import Expr, Field, from_form
from wequant.formula import compile_formula, format_formula
from wequant.service.templates import TEMPLATES


def round_trip(tree: Expr) -> str:
    text, period = format_formula(tree)
    return compile_formula(text, period).expr.key


@pytest.mark.parametrize("tree", [tree for _, _, tree in SAMPLES], ids=[f"{i}" for i in range(len(SAMPLES))])
def test_a_sample_tree_formats_and_compiles_back(tree):
    assert round_trip(tree) == tree.key


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_expression_formats_and_compiles_back(name):
    form = TEMPLATES[name][2]
    exprs = [f["expr"] for f in form.get("factors", [])] + [c["expr"] for c in form.get("filters", [])]
    assert exprs and all(round_trip(from_form(e)) == from_form(e).key for e in exprs)


@settings(max_examples=300, deadline=None)
@given(trees(DEPTH))
def test_a_random_tree_formats_and_compiles_back(tree):
    assume("null" not in tree.key)  # an empty argument has no text
    assert round_trip(tree) == tree.key


def test_names_read_as_tdx_writes_them():
    """Back-adjusted prices as CLOSE, volume in lots as VOL, the unadjusted with RAW_, families as functions."""
    text, period = format_formula(from_form(["WEEK", ["GT", "adj_close", ["DIV", "volume", 100.0]]]))
    assert (text, period) == ("CLOSE > VOL", "week")
    assert format_formula(Field("close") > 5)[0] == "RAW_CLOSE > 5"
    assert format_formula(~Field("exchange@BJ"))[0] == "NOT EXCHANGE('BJ')"
