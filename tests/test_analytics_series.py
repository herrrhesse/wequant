"""The benchmark line's levels (S2.10b): drawn from the benchmark's first close, at the strategy's nav that day."""

import numpy as np

from wequant.analytics.series import relative_levels

NAN = np.nan


def test_a_benchmark_with_a_close_on_the_base_day_keeps_its_old_line_bit_for_bit():
    """A run whose benchmark has a close on its first day draws exactly levels / levels[0], as before S2.10b."""
    levels = np.array([3.7, 4.1, NAN, 3.3, 5.9, 2.9])
    nav = np.array([1.0, 1.013, 0.97, 1.21, 0.88, 1.4])
    assert np.array_equal(relative_levels(levels, nav), levels / levels[0], equal_nan=True)


def test_only_the_days_before_the_first_close_and_the_days_without_one_have_no_level():
    """Days missing between closes stay missing; every other day after the first close has a level."""
    levels = np.array([NAN, NAN, 10.0, NAN, NAN, 12.0, NAN, 11.0])
    line = relative_levels(levels, np.linspace(1.0, 1.7, 8))
    assert np.isnan(line).tolist() == [True, True, False, True, True, False, True, False]
