"""Hand-computed values: one stock with a suspended day, masks, cross sections and a TDX indicator.

Each value pins a TDX convention independently of tests/ops/reference.py: windows count bars, STD is the sample
deviation, EMA starts at the first bar, SUM(x, 0) is anchored at the first row, ties go to the latest bar and TS_RANK
gives ties their average rank.
"""

import numpy as np
import pytest

from wequant.ops import (
    ABS,
    BARSLAST,
    CORR,
    COUNT,
    CROSS,
    CS_PCT,
    CS_RANK,
    CS_ZSCORE,
    DIV,
    EMA,
    EVERY,
    HHVBARS,
    IF,
    MA,
    MAX,
    REF,
    SMA,
    STD,
    SUM,
    TS_RANK,
    VALUEWHEN,
)

nan = np.nan
X = np.array([10, 11, 12, 11, 13, nan, 14, 12.0])  # row 5 suspended
TIE = np.array([5, 7, 7, nan, 6.0])  # the highest of rows 1 and 2 is tied
CLOSE = np.array([10.00, 10.20, 10.10, 10.40, 10.30, 10.60, 10.50, 10.80, 10.70, 11.00])


def same(actual, expected, rel=1e-12):
    np.testing.assert_allclose(actual, np.asarray(expected, dtype=float), rtol=rel, atol=0)


@pytest.mark.parametrize(
    ("op", "n", "x", "expected"),
    [
        (MA, 3, X, [nan, nan, 11, 34 / 3, 12, nan, 38 / 3, 13]),
        (SUM, 0, X, [10, 21, 33, 44, 57, nan, 71, 83]),
        (EMA, 3, X, [10, 10.5, 11.25, 11.125, 12.0625, nan, 13.03125, 12.515625]),
        (STD, 3, X, [nan, nan, 1, np.sqrt(1 / 3), 1, nan, np.sqrt(7 / 3), 1]),
        (TS_RANK, 3, X, [nan, nan, 1, 0.5, 1, nan, 1, 1 / 3]),
        (HHVBARS, 3, TIE, [nan, nan, 0, nan, 1]),
    ],
    ids=["MA-3", "SUM-0", "EMA-3", "STD-3", "TS_RANK-3", "HHVBARS-tie"],
)
def test_windows_count_bars_not_rows(op, n, x, expected):
    same(op(x, n), expected)


def test_window_sums_keep_no_rounding_error_from_older_bars():
    x = np.array([10.23, 10.45, 9.87, 10.01, 10.33, 10.29, 10.5, 10.25, 10.25, 10.25])
    assert np.array_equal(MA(x, 1), x) and np.array_equal(SUM(x, 1), x)
    assert MA(x, 3)[-1] == 10.25  # 3 × 10.25 is exact in binary


def test_a_bool_mask_reaches_conditions_only_through_traded_days():
    mask, traded = np.array([True, False, True, True]), np.array([True, True, False, True])
    same(IF(traded, mask, nan), [1, 0, nan, 1])
    same(COUNT(IF(traded, mask, nan), 2), [nan, 1, nan, 1])
    for call in (lambda: COUNT(mask, 2), lambda: BARSLAST(mask), lambda: EVERY(mask, 2)):
        with pytest.raises(TypeError):
            call()
    with pytest.raises(TypeError):
        VALUEWHEN(mask, np.arange(4.0))


def test_cross():
    same(CROSS(X, 11.5), [0, 0, 1, 0, 1, nan, 0, 0])
    same(CROSS(np.array([1, 2, 2, 3.0]), np.array([2, 2, 2, 2.0])), [0, 0, 0, 1])
    fired = CROSS(np.arange(12.0).reshape(6, 2), 5.0)
    same(fired, np.where(np.arange(6)[:, None] == 3, 1.0, 0.0) * np.ones((1, 2)))


def test_cross_sections():
    row = np.array([[3, 1, nan, 3, 2.0]])
    same(CS_RANK(row), [[3.5, 1, nan, 3.5, 2]])
    same(CS_PCT(row), [[0.875, 0.25, nan, 0.875, 0.5]])
    single = np.array([[nan, 4.0, nan]])
    same(CS_RANK(single), [[nan, 1, nan]])
    same(CS_PCT(single), [[nan, 1, nan]])


def test_equal_values_have_no_spread():
    """The mean of equal values like 0.1 is rounded, so their deviations are tiny but not 0."""
    same(CORR(np.array([0.1, 0.1, 0.1, 1.0]), np.array([2, 3, 4, 5.0]), 3), [nan, nan, nan, np.sqrt(3) / 2])
    assert np.isnan(CS_ZSCORE(np.full((1, 3), 0.1))).all()


def test_rsi():
    change = CLOSE - REF(CLOSE, 1)
    rsi = DIV(SMA(MAX(change, 0), 6, 1), SMA(ABS(change), 6, 1)) * 100
    expected = [100, 90.9090909091, 93.1506849315, 84.7880299252, 88.5035808519, 80.6263306092, 85.3276190278]
    same(rsi, [nan, *expected, 77.7780516991, 83.1462774992], rel=1e-9)
