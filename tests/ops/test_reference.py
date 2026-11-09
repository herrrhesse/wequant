"""Every operator matches its naive reference on random grids with realistic gaps."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.ops.panels import cases
from tests.ops.reference import REGISTRY, ROLES
from wequant import ops

EXACT = {
    *("MA", "SUM"),  # compensated sums round once, like fsum and fmean
    *("REF", "HHV", "LLV", "HHVBARS", "LLVBARS", "COUNT", "EVERY", "EXIST", "TS_RANK"),
    *("CROSS", "BARSLAST", "BARSCOUNT", "VALUEWHEN", "CS_RANK", "CS_PCT"),
    *("GT", "GE", "LT", "LE", "EQ", "AND", "OR", "NOT", "IF", "BETWEEN", "MAX", "MIN", "ABS"),
}


def test_the_registry_covers_every_operator():
    assert len(ops.__all__) == 42
    assert set(REGISTRY) == set(ops.__all__)


@pytest.mark.parametrize("name", list(REGISTRY))
@given(data=st.data())
def test_matches_the_reference(name, data):
    op = REGISTRY[name]
    arguments, window = data.draw(cases(ROLES[op.kind], op.minimum))
    actual, expected = op.call(*arguments, **window), op.reference(*arguments, **window)
    if name in EXACT:
        np.testing.assert_array_equal(actual, expected)
    else:
        assert_close(actual, expected, arguments)


def assert_close(actual, expected, arguments):
    """The same NaN cells, and values within 1e-9 relative or 1e-9 of the largest input."""
    np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
    scale = max(np.abs(a[np.isfinite(a)]).max(initial=0.0) for a in map(np.asarray, arguments))
    np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-9 * scale)
