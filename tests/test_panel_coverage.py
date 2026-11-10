import numpy as np

from wequant.panel.coverage import count_spans

DATES = np.arange("2024-01-01", "2024-01-08", dtype="datetime64[D]")


def test_spans_are_runs_of_one_count_off_the_nominal_size():
    spans = count_spans(DATES, [300, 300, 299, 299, 301, 300, 0], 300)
    assert [(str(s.start), str(s.end), s.days, s.count, s.kind) for s in spans] == [
        ("2024-01-03", "2024-01-04", 2, 299, "short"),
        ("2024-01-05", "2024-01-05", 1, 301, "over"),
        ("2024-01-07", "2024-01-07", 1, 0, "empty"),
    ]


def test_without_a_nominal_size_only_empty_days_are_off():
    spans = count_spans(DATES, [5, 0, 0, 3, 3, 4, 2], None)
    assert [(str(s.start), s.days, s.kind) for s in spans] == [("2024-01-02", 2, "empty")]
