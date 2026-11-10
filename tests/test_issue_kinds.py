"""The data_issues kinds (design 15.1, prompt A-1): which kinds are anomalies, and no writer adds an unclassified kind."""

import pytest

from wequant.data.issues import check_kind
from wequant.data.schema import CLASSES, ISSUE_KINDS, LEVELS, NOT_ANOMALOUS_ACTIONS, TABLES

ANOMALY_TABLES = ("bars", "adj_factor", "daily_status")


@pytest.mark.parametrize(
    "written",
    [
        ("bars", "no_such_kind", "flagged"),  # an unknown kind
        ("bars", "adj_blip", "excluded_from_adj_factor_and_preclose"),  # a known kind on another table
        ("bars", "off_scale", "flagged"),  # a known kind with another action
    ],
)
def test_a_writer_cannot_write_a_kind_issue_kinds_lacks(written):
    """A-1: every written (table, kind, action) is in ISSUE_KINDS, so each anomaly has a class and a level."""
    with pytest.raises(ValueError, match="add it to ISSUE_KINDS"):
        check_kind(*written)


def test_exactly_the_kinds_on_cells_a_run_reads_are_anomalies_each_with_a_level():
    """Design 15.1: a row is an anomaly iff its table is bars, adj_factor or daily_status and its actions are not the
    four that remove whole codes, spans, duplicates or early status rows."""
    for name, kind in ISSUE_KINDS.items():
        assert kind.table in TABLES and (kind.anomaly is None) == (kind.level is None), name
        reads = kind.table in ANOMALY_TABLES and not set(kind.actions) <= set(NOT_ANOMALOUS_ACTIONS)
        assert (kind.anomaly in CLASSES) == reads and kind.level in (*LEVELS, None), name
