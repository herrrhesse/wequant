import numpy as np
import pytest

from wequant.analytics.series import annualize, compound, drawdowns, period_keys, runs, simple_returns


def days(*isos: str) -> np.ndarray:
    return np.array(isos, dtype="datetime64[D]")


def test_simple_returns():
    np.testing.assert_allclose(simple_returns(np.array([100.0, 110.0, 99.0])), [0.1, -0.1])


def test_a_nan_level_voids_its_day_and_the_next():
    returns = simple_returns(np.array([100.0, 110.0, np.nan, 99.0, 108.9]))
    np.testing.assert_allclose(returns, [0.1, np.nan, np.nan, 0.1])


def test_drawdowns():
    np.testing.assert_allclose(drawdowns(np.array([1.0, 0.9, 0.95])), [0.0, 0.1, 0.05])


def test_runs():
    starts, stops = runs(np.array([False, True, True, False, True]))
    assert starts.tolist() == [1, 4] and stops.tolist() == [3, 5]


def test_runs_of_all_false_and_all_true():
    assert [a.tolist() for a in runs(np.zeros(4, dtype=bool))] == [[], []]
    assert [a.tolist() for a in runs(np.ones(4, dtype=bool))] == [[0], [4]]


def test_week_keys_are_mondays():
    keys = period_keys(days("1970-01-01", "2024-02-04", "2024-02-05", "1960-03-03"), "week")
    assert keys.tolist() == days("1969-12-29", "2024-01-29", "2024-02-05", "1960-02-29").tolist()


def test_month_keys():
    keys = period_keys(days("2024-02-29", "2024-03-01", "1969-12-31"), "month")
    assert keys[0] != keys[1]
    assert keys.astype(str).tolist() == ["2024-02", "2024-03", "1969-12"]


def test_unknown_period_raises():
    with pytest.raises(ValueError, match="period"):
        period_keys(days("2024-01-02"), "quarter")


def test_compound():
    np.testing.assert_allclose(compound(np.array([0.1, -0.1, 0.0, 0.2]), np.array(list("aabc"))), [-0.01, 0.0, 0.2])


def test_a_nan_day_voids_its_group():
    result = compound(np.array([0.1, np.nan, 0.0, 0.2]), np.array(list("aabc")))
    np.testing.assert_allclose(result, [np.nan, 0.0, 0.2])


def test_compound_of_nothing_is_empty():
    assert compound(np.array([]), np.array([])).size == 0


def test_annualize():
    assert annualize(1.188, 4, 4) == pytest.approx(0.188)
    assert annualize(1.21, 8, 4) == pytest.approx(0.1)
