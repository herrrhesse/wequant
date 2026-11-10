import numpy as np

from tests.test_panel import DAYS, A, seed
from wequant.panel import build
from wequant.panel.cache import PanelCache
from wequant.panel.status import MISSING, NO_ROW, NOT_LISTED, SUSPENDED, TRADED, classify

DATES = np.array(DAYS, dtype="datetime64[D]")
CODES = np.array(["000001.SZ", "600000.SH"])


def test_the_later_of_two_events_on_one_row_wins(tmp_path):
    events = [
        {"code": A, "ex_date": "2024-01-07", "factor": 3.0},
        {"code": A, "ex_date": "2024-01-06", "factor": 2.0},
        {"code": A, "ex_date": "2024-01-02", "factor": 1.0},
        {"code": A, "ex_date": "2024-02-01", "factor": 9.0},
    ]
    store = seed(tmp_path / "home", adj_factor=events)
    src = PanelCache(store, store.manifest(), tmp_path / "cache")
    factor = build.factor(src)[:, list(src.array("codes")).index(A)]
    np.testing.assert_array_equal(factor, [1, 1, 1, 1, 3, 3, 3, 3])
    assert (build.factor(src)[:, [c != A for c in src.array("codes")]] == 1.0).all()


def test_classify_applies_the_first_matching_rule():
    bar = np.array([[1, 1, 0, 0, 0, 0, 0, 0, 0]], dtype=bool)
    tradestatus = np.array([[0, NO_ROW, 0, 0, 1, NO_ROW, NO_ROW, NO_ROW, NO_ROW]], dtype=np.int8)
    dropped = np.array([[1, 0, 1, 0, 0, 1, 1, 0, 0]], dtype=bool)
    span = np.array([[1, 1, 1, 0, 0, 0, 1, 1, 0]], dtype=bool)
    expected = [TRADED, TRADED, SUSPENDED, SUSPENDED, MISSING, MISSING, MISSING, SUSPENDED, NOT_LISTED]
    status = classify(bar, tradestatus, dropped, span)
    assert status.dtype == np.int8 and status.tolist() == [expected]
