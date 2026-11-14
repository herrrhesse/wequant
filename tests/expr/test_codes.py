"""CODES (round 3): a fixed list of stocks as a condition, for a strategy's custom stock list."""

import numpy as np

from tests.expr.conftest import SH, SZ
from wequant.expr import Codes, evaluate, from_form

NAN = np.nan


def test_a_fixed_list_is_true_on_its_codes_bars_and_false_on_other_bars(panel):
    """600000.SH has no bar on D3 (tests.expr.conftest.CLOSES)."""
    grid = evaluate(Codes([SZ]), panel)
    columns = {code: grid[:, i] for i, code in enumerate(panel.codes)}
    np.testing.assert_array_equal(columns[SZ], [1.0] * 6)
    np.testing.assert_array_equal(columns[SH], [0.0, 0.0, 0.0, NAN, 0.0, 0.0])


def test_a_fixed_list_has_one_form_whatever_the_order_and_repeats():
    assert from_form(["CODES", SH, SZ, SZ]).key == Codes([SZ, SH]).key == f'["CODES","{SZ}","{SH}"]'
