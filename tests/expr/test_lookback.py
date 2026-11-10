"""Design 15.2: how many bars back a root reads, by operator family, and which fields it names."""

import pytest

from wequant.expr import CROSS, CS_RANK, EMA, IF, MA, REF, SUM, Field
from wequant.expr.lookback import fields, lookback

CLOSE, OPEN = Field("close"), Field("open")


@pytest.mark.parametrize(
    ("expr", "bars"),
    [
        (CLOSE, 0),  # a field reads its own bar
        (REF(MA(CLOSE, 3), 3), 5),  # n bars back, on top of its argument's reach
        (MA(REF(CLOSE, 1), 5), 5),  # a window of n spans n - 1 bars, on top of its argument's reach
        (CROSS(CLOSE, MA(OPEN, 3)), 3),  # a cross compares with the bar before
        (CS_RANK(CLOSE / REF(OPEN, 2)), 2),  # cross-sectional and element-wise operators add nothing
        (SUM(CLOSE, 0), None),  # a window of 0 bars is the whole history
        (MA(EMA(CLOSE, 3), 2), None),  # a recursive operator reads from the first row
    ],
)
def test_lookback_by_operator_family(expr, bars):
    assert lookback(expr) == bars


def test_fields_are_every_field_a_root_names():
    assert fields(IF(Field("st"), REF(CLOSE, 1), Field("amount"))) == {"st", "close", "amount"}
