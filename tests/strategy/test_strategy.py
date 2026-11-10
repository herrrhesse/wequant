"""Strategy descriptions: every validation rule names its field, values are normalised, steps come in funnel order."""

import datetime as dt
import math

import pytest

from wequant.expr import Field
from wequant.strategy import Condition, Factor, Group, Pool, SpecError, SpecErrors, Step, Strategy

CLOSE, ST = Field("close"), Field("st")
OK = Condition("ok", CLOSE > 1.0)
MOM = Factor("mom", CLOSE)


TOP2 = {"factors": (MOM,), "top_n": 2}


# One case per generic rule (the loc prefix comes from Checked for every field alike), every rule across fields, and
# the rules with their own locating logic: duplicate names and board indexes.
RULES = {
    "empty_name": (lambda: Condition("", CLOSE > 1.0), "out_of_range", ("name",)),
    "reserved_name": (lambda: Condition("min_list_days", CLOSE > 1.0), "reserved_name", ("name",)),
    "needs_condition": (lambda: Condition("c", CLOSE), "needs_condition", ("expr",)),
    "flag_not_int": (lambda: Condition("c", CLOSE > 1.0, enabled=1), "wrong_type", ("enabled",)),
    "group_needs_a_condition": (lambda: Group("g", ()), "out_of_range", ("conditions",)),
    "not_a_choice": (lambda: Group("g", (OK,), mode="some"), "not_a_choice", ("mode",)),
    "number_positive": (lambda: Factor("f", CLOSE, weight=0), "out_of_range", ("weight",)),
    "number_nan": (lambda: Factor("f", CLOSE, weight=math.nan), "out_of_range", ("weight",)),
    "number_not_bool": (lambda: Factor("f", CLOSE, weight=True), "wrong_type", ("weight",)),
    "duplicate_factor": (
        lambda: Strategy(filters=(OK,), factors=(Factor("ok", CLOSE),)),
        "duplicate_name",
        ("factors", 0, "name"),
    ),
    "duplicate_filter": (lambda: Strategy(filters=(Group("g", (OK,)), OK)), "duplicate_name", ("filters", 1, "name")),
    "duplicate_member": (
        lambda: Strategy(filters=(Group("g", (Condition("g", CLOSE > 1.0),)),)),
        "duplicate_name",
        ("filters", 0, "conditions", 0, "name"),
    ),
    "count_not_bool": (lambda: Strategy(factors=(MOM,), top_n=True), "wrong_type", ("top_n",)),
    "top_n_needs_a_factor": (lambda: Strategy(top_n=2), "requires", ("top_n",)),
    "top_n_needs_an_enabled_factor": (
        lambda: Strategy(factors=(Factor("mom", CLOSE, enabled=False),), top_n=2),
        "requires",
        ("top_n",),
    ),
    "backups_need_top_n": (lambda: Strategy(factors=(MOM,), backups=1), "requires", ("backups",)),
    "score_needs_a_factor": (lambda: Strategy(weighting="score"), "requires", ("weighting",)),
    "formula_without_formula_weighting": (lambda: Strategy(formula=CLOSE), "requires", ("formula",)),
    "formula_weighting_without_formula": (lambda: Strategy(weighting="formula"), "requires", ("formula",)),
    "date_not_datetime": (lambda: Strategy(anchor=dt.datetime(2024, 1, 2)), "wrong_type", ("anchor",)),
    "hold_rank_needs_top_n": (lambda: Strategy(factors=(MOM,), hold_rank=3), "requires", ("hold_rank",)),
    "hold_rank_above_top_n": (lambda: Strategy(**TOP2, hold_rank=2), "out_of_range", ("hold_rank",)),
    "max_weight_0": (lambda: Strategy(max_weight=0), "out_of_range", ("max_weight",)),
    "max_weight_1": (lambda: Strategy(max_weight=1), "out_of_range", ("max_weight",)),
    "board_not_a_choice": (lambda: Pool(boards=("main", "bse")), "not_a_choice", ("boards", 1)),
    "board_twice": (lambda: Pool(boards=("star", "main", "star")), "duplicate_value", ("boards", 2)),
    "min_list_days_1": (lambda: Pool(min_list_days=1), "out_of_range", ("min_list_days",)),
}


@pytest.mark.parametrize(("build", "code", "loc"), list(RULES.values()), ids=list(RULES))
def test_each_rule_raises_its_code_at_its_location(build, code, loc):
    with pytest.raises((SpecError, SpecErrors)) as raised:
        build()
    (error,) = raised.value.errors
    assert (error.code, error.loc, error.path) == (code, loc, ())
    assert str(error).startswith(".".join(map(str, loc)) + ": ")


def test_steps_in_funnel_order_without_disabled_items_and_empty_groups():
    """The funnel: enabled filters in order, a group's enabled members before the group, disabled and empty ones out."""
    a, b = Condition("a", CLOSE > 1.0), Condition("b", ~ST)
    off = Condition("off", CLOSE > 2.0, enabled=False)
    strategy = Strategy(
        filters=(
            a,
            off,
            Group("g", (b, Condition("c", CLOSE > 3.0, enabled=False), Condition("d", CLOSE > 4.0)), mode="any"),
            Group("empty", (Condition("e", CLOSE > 5.0, enabled=False),)),
            Group("disabled", (Condition("f", CLOSE > 6.0),), enabled=False),
        )
    )
    assert strategy.steps() == (
        Step("a", a.expr, (), "all", True),
        Step("b", b.expr, (), "all", False),
        Step("d", CLOSE > 4.0, (), "all", False),
        Step("g", None, ("b", "d"), "any", True),
    )
