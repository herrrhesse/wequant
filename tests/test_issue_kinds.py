"""The data_issues kinds: each with its table, actions, anomaly class and level, and every writer's rows among them."""

import pytest

from wequant.data.issues import check_kind
from wequant.data.merge.audit import FLAGGED
from wequant.data.schema import CLASS_DOCS, CLASSES, ISSUE_KINDS, LEVEL_DOCS, LEVELS, NOT_ANOMALOUS_ACTIONS, TABLES
from wequant.rules.check import ACTION, KIND

ANOMALY_TABLES = ("bars", "adj_factor", "daily_status")
WRITTEN = [  # (table, kind, action) of each writer; tools/investment_data checks its own rows
    ("bars", "off_scale", "row_dropped"),
    ("bars", "removed_security", "security_removed"),
    ("adj_factor", "adj_blip", "excluded_from_adj_factor_and_preclose"),
    ("index_members", "invalid_member_code", "span_dropped"),
    ("bars", "bar_added", "row_added"),
    ("bars", "bar_unconfirmed", "row_dropped"),
    *(("bars", kind, "value_replaced") for kind in ("volume_mismatch", "amount_mismatch", "preclose_mismatch")),
    ("adj_factor", "event_unconfirmed", "event_dropped"),
    ("adj_factor", "event_added", "event_added"),
    ("adj_factor", "step_mismatch", "value_replaced"),
    *(("adj_factor", kind, "flagged") for kind in FLAGGED),
    ("bars", "price_mismatch", "resolved_baostock"),
    ("bars", "price_mismatch", "resolved_seed"),
    ("bars", "average_price", "resolved_seed"),
    ("bars", "average_price", "resolved_null"),
    ("bars", "close_unconfirmed", "flagged"),
    ("bars", "duplicate_code", "duplicate_dropped"),
    ("daily_status", "before_first_bar", "status_dropped"),
    ("daily_status", "tradestatus_conflict", "bar_kept"),
    ("daily_status", "tradestatus_conflict", "no_bar"),
    ("securities", "no_bars", "security_removed"),
    ("bars", KIND, ACTION),
]


@pytest.mark.parametrize("written", WRITTEN)
def test_every_writer_writes_a_known_kind(written):
    check_kind(*written)


def test_every_kind_is_written_and_unknown_ones_are_refused():
    assert {kind for _, kind, _ in WRITTEN} == ISSUE_KINDS.keys()
    assert {(t, k, a) for k, i in ISSUE_KINDS.items() for a in i.actions for t in [i.table]} == set(WRITTEN)
    with pytest.raises(ValueError, match="add it to ISSUE_KINDS"):
        check_kind("bars", "adj_blip", "excluded_from_adj_factor_and_preclose")
    with pytest.raises(ValueError, match="no data_issues kind 'off_scale' with action 'flagged'"):
        check_kind("bars", "off_scale", "flagged")


def test_an_anomaly_has_a_class_and_a_level_and_only_cells_a_run_reads_are_anomalies():
    for name, kind in ISSUE_KINDS.items():
        assert kind.table in TABLES and kind.actions and kind.doc, name
        assert (kind.anomaly is None) == (kind.level is None), name
        reads = kind.table in ANOMALY_TABLES and not set(kind.actions) <= set(NOT_ANOMALOUS_ACTIONS)
        assert (kind.anomaly in CLASSES) == reads and kind.level in (*LEVELS, None), name
    assert CLASS_DOCS.keys() == set(CLASSES) and LEVEL_DOCS.keys() == set(LEVELS)


def test_the_levels_the_decision_named_and_the_verified_errors():
    level = {name: kind.level for name, kind in ISSUE_KINDS.items()}
    assert level["bar_added"] == level["event_added"] == level["limit_breach"] == "notice"
    assert [name for name, kind in level.items() if kind == "error"] == ["price_mismatch", "average_price"]
    assert all(ISSUE_KINDS[name].actions[0].startswith("resolved_") for name in ("price_mismatch", "average_price"))


def test_the_column_docs_come_from_the_kinds():
    columns = {column.name: column.doc for column in TABLES["data_issues"].columns}
    assert all(kind in columns["kind"] for kind in ISSUE_KINDS)
    assert all(action in columns["action"] for kind in ISSUE_KINDS.values() for action in kind.actions)
    states = " / ".join(name for name, kind in ISSUE_KINDS.items() if kind.state)
    assert states == "event_missing / event_unimplied / event_step_off / limit_breach"
    assert f"State kinds ({states})" in TABLES["data_issues"].doc
