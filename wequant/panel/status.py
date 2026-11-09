"""Day status of each (date, code) cell, by the first matching rule."""

from __future__ import annotations

import numpy as np

NOT_LISTED, TRADED, SUSPENDED, MISSING = 0, 1, 2, 3
NO_ROW = -1  # tradestatus and st grid value where daily_status has no row


def classify(bar: np.ndarray, tradestatus: np.ndarray, dropped: np.ndarray, span: np.ndarray) -> np.ndarray:
    """int8 status: a bar, then daily_status, then a dropped bar, then the data span decide."""
    rules = [bar, tradestatus == 0, (tradestatus == 1) | dropped, span]
    return np.select(rules, [TRADED, SUSPENDED, MISSING, SUSPENDED], NOT_LISTED).astype(np.int8)
