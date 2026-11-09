"""Hand-computed values: one stock with a suspended day, masks, cross sections and TDX indicators."""

import numpy as np
import pytest

from wequant.ops import (
    ABS,
    AND,
    AVEDEV,
    BARSCOUNT,
    BARSLAST,
    BETWEEN,
    CORR,
    COUNT,
    CROSS,
    CS_DEMEAN,
    CS_PCT,
    CS_RANK,
    CS_ZSCORE,
    DIV,
    EMA,
    EVERY,
    EXIST,
    GT,
    HHV,
    HHVBARS,
    IF,
    LLV,
    LLVBARS,
    LN,
    MA,
    MAX,
    NOT,
    OR,
    REF,
    SLOPE,
    SMA,
    STD,
    STDP,
    SUM,
    TS_RANK,
    VALUEWHEN,
    WMA,
)

nan = np.nan
X = np.array([10, 11, 12, 11, 13, nan, 14, 12.0])  # row 5 suspended
C = GT(X, REF(X, 1))
CLOSE = np.array([10.00, 10.20, 10.10, 10.40, 10.30, 10.60, 10.50, 10.80, 10.70, 11.00])


def same(actual, expected, rel=1e-12):
    np.testing.assert_allclose(actual, np.asarray(expected, dtype=float), rtol=rel, atol=0)


@pytest.mark.parametrize(
    ("op", "n", "expected"),
    [
        (MA, 3, [nan, nan, 11, 34 / 3, 12, nan, 38 / 3, 13]),
        (SUM, 3, [nan, nan, 33, 34, 36, nan, 38, 39]),
        (SUM, 0, [10, 21, 33, 44, 57, nan, 71, 83]),
        (EMA, 3, [10, 10.5, 11.25, 11.125, 12.0625, nan, 13.03125, 12.515625]),
        (REF, 1, [nan, 10, 11, 12, 11, nan, 13, 14]),
        (REF, 2, [nan, nan, 10, 11, 12, nan, 11, 13]),
        (HHV, 3, [nan, nan, 12, 12, 13, nan, 14, 14]),
        (LLV, 3, [nan, nan, 10, 11, 11, nan, 11, 12]),
        (HHV, 0, [10, 11, 12, 12, 13, nan, 14, 14]),
        (HHVBARS, 3, [nan, nan, 0, 1, 0, nan, 0, 1]),
        (LLVBARS, 3, [nan, nan, 2, 0, 1, nan, 2, 0]),
        (STD, 3, [nan, nan, 1, np.sqrt(1 / 3), 1, nan, np.sqrt(7 / 3), 1]),
        (AVEDEV, 3, [nan, nan, 2 / 3, 4 / 9, 2 / 3, nan, 10 / 9, 2 / 3]),
        (WMA, 3, [nan, nan, 68 / 6, 68 / 6, 73 / 6, nan, 79 / 6, 77 / 6]),
        (SLOPE, 3, [nan, nan, 1, 0, 0.5, nan, 1.5, -0.5]),
        (TS_RANK, 3, [nan, nan, 1, 0.5, 1, nan, 1, 1 / 3]),
    ],
)
def test_windows_count_bars_not_rows(op, n, expected):
    same(op(X, n), expected)


def test_window_sums_keep_no_rounding_error_from_older_bars():
    x = np.array([10.23, 10.45, 9.87, 10.01, 10.33, 10.29, 10.5, 10.25, 10.25, 10.25])
    assert np.array_equal(MA(x, 1), x) and np.array_equal(SUM(x, 1), x)
    assert MA(x, 3)[-1] == 10.25  # 3 × 10.25 is exact in binary


def test_stdp_and_sma():
    same(STDP(X, 3), [nan, nan, 0.8164965809, 0.4714045208, 0.8164965809, nan, 1.2472191289, 0.8164965809], rel=1e-9)
    same(SMA(X, 3, 1), [10, 31 / 3, 98 / 9, 295 / 27, 941 / 81, nan, 3016 / 243, 8948 / 729])


def test_highest_bars_prefer_the_latest_tie():
    same(HHVBARS(np.array([5, 7, 7, nan, 6.0]), 3), [nan, nan, 0, nan, 1])


def test_barscount_counts_bars():
    same(BARSCOUNT(X), [1, 2, 3, 4, 5, nan, 6, 7])


def test_conditions():
    same(C, [nan, 1, 1, 0, 1, nan, 1, 0])
    same(COUNT(C, 3), [nan, nan, nan, 2, 2, nan, 2, 2])
    same(EVERY(C, 2), [nan, nan, 1, 0, 0, nan, 1, 0])
    same(EXIST(C, 2), [nan, nan, 1, 1, 1, nan, 1, 1])


def test_events_are_nan_before_the_first_true_bar():
    same(BARSLAST(C), [nan, 0, 0, 1, 0, nan, 0, 1])
    same(BARSLAST(np.array([1, 0, nan, 0, 1, 0.0])), [0, 1, nan, 2, 0, 1])
    same(BARSLAST(np.array([0, 0, 1, 0.0])), [nan, nan, 0, 1])
    same(VALUEWHEN(C, X), [nan, 11, 12, 12, 13, nan, 14, 14])
    same(VALUEWHEN(np.array([0, 1, 0.0]), np.array([5, 6, 7.0])), [nan, 6, 6])


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
    same(CS_DEMEAN(row), [[0.75, -1.25, nan, 0.75, -0.25]])
    same(CS_ZSCORE(row), [[0.9045340337, -1.5075567229, nan, 0.9045340337, -0.3015113446]], rel=1e-9)
    for op in (CS_RANK, CS_PCT, CS_DEMEAN, CS_ZSCORE):
        assert np.isnan(op(np.full((1, 3), nan))).all()
    single = np.array([[nan, 4.0, nan]])
    same(CS_RANK(single), [[nan, 1, nan]])
    same(CS_PCT(single), [[nan, 1, nan]])
    assert np.isnan(CS_ZSCORE(single)).all()


def test_equal_values_have_no_spread():
    """The mean of equal values like 0.1 is rounded, so their deviations are tiny but not 0."""
    same(CORR(np.array([0.1, 0.1, 0.1, 1.0]), np.array([2, 3, 4, 5.0]), 3), [nan, nan, nan, np.sqrt(3) / 2])
    assert np.isnan(CS_ZSCORE(np.full((1, 3), 0.1))).all()


def test_logic_and_arithmetic():
    for result in (IF(nan, 1, 0), AND(nan, 1), OR(nan, 1), NOT(nan), MAX(nan, 1), DIV(1, 0), LN(0), ABS(-np.inf)):
        assert np.isnan(result)
    assert BETWEEN(5, 7, 3) == 1
    assert IF(1, 2.0, nan) == 2


def test_macd():
    dif = EMA(CLOSE, 12) - EMA(CLOSE, 26)
    dea = EMA(dif, 9)
    macd = 2 * (dif - dea)
    same(dif[[1, 9]], [0.0159544159544, 0.201399314271], rel=1e-9)
    same(dea[9], 0.112124766584, rel=1e-9)
    same(macd[9], 0.178549095374, rel=1e-9)


def test_rsi():
    change = CLOSE - REF(CLOSE, 1)
    rsi = DIV(SMA(MAX(change, 0), 6, 1), SMA(ABS(change), 6, 1)) * 100
    expected = [100, 90.9090909091, 93.1506849315, 84.7880299252, 88.5035808519, 80.6263306092, 85.3276190278]
    same(rsi, [nan, *expected, 77.7780516991, 83.1462774992], rel=1e-9)


def test_boll():
    mid = MA(CLOSE, 5)
    upper = mid + 2 * STD(CLOSE, 5)
    same(mid[4:], [10.2, 10.32, 10.38, 10.52, 10.58, 10.72], rel=1e-9)
    same(upper[4:], [10.516227766, 10.7047076812, 10.7647076812, 10.9047076812, 10.9647076812, 11.1047076812], rel=1e-9)
