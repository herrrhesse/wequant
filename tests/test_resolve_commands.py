"""data resolve list | approve | reject through the service and the command line (synthetic entries)."""

import datetime as dt

import pytest

from tests.test_merge_resolutions import EARLY, LATE
from wequant.cli import main
from wequant.data.merge.resolutions import add, default_path, load
from wequant.data.store import exclusive
from wequant.service import (
    Context,
    Decision,
    DecisionRequest,
    Exit,
    ResolveListRequest,
    ResolvePage,
    ServiceError,
    data_resolve_approve,
    data_resolve_list,
    data_resolve_reject,
)

PROPOSED = {"status": "proposed", "approval": ""}
B1 = EARLY | PROPOSED | {"category": "B"}
B2 = B1 | {"date": dt.date(2023, 12, 1)}
C1 = LATE | {"category": "C"}


@pytest.fixture
def home(tmp_path):
    add(default_path(tmp_path), [B1, B2, C1])
    return tmp_path


def test_a_decision_takes_the_entries_every_selector_matches(home):
    request = DecisionRequest(category="B", start="2023-11-30", note="user: checked")
    assert data_resolve_approve(request, Context(home)) == Decision(status="approved", matched=1, changed=1)
    assert load(default_path(home)).select("date", "status").rows() == [
        (dt.date(2023, 11, 7), "proposed"),
        (dt.date(2023, 12, 1), "approved"),
        (dt.date(2024, 1, 2), "proposed"),
    ]


def test_a_decision_without_a_selector_is_refused(home):
    with pytest.raises(ServiceError) as raised:
        data_resolve_reject(DecisionRequest(note="user"), Context(home))
    assert raised.value.exit == Exit.USAGE and raised.value.detail[0].code == "requires"


def test_approving_an_undetermined_entry_is_a_usage_error(tmp_path):
    add(default_path(tmp_path), [C1 | {"take": "undetermined"}])
    with pytest.raises(ServiceError) as raised:
        data_resolve_approve(DecisionRequest(category="C", note="user"), Context(tmp_path))
    assert raised.value.exit == Exit.USAGE and raised.value.detail[0].code == "undetermined"


def test_a_decision_without_a_resolutions_file_is_not_found(tmp_path):
    with pytest.raises(ServiceError) as raised:
        data_resolve_approve(DecisionRequest(category="B", note="user"), Context(tmp_path))
    assert raised.value.exit == Exit.NOT_FOUND


def test_a_decision_while_the_file_is_locked_is_refused(home):
    with exclusive(default_path(home).parent / ".lock"), pytest.raises(ServiceError) as raised:
        data_resolve_approve(DecisionRequest(category="B", note="user"), Context(home))
    assert raised.value.exit == Exit.LOCKED


def test_the_list_filters_by_status_and_counts_every_match_by_category(home):
    data_resolve_reject(DecisionRequest(code="000001.SZ", note="user"), Context(home))
    page = data_resolve_list(ResolveListRequest(limit=1), Context(home))
    assert (page.total, [r.date for r in page.rows]) == (3, [dt.date(2023, 11, 7)])
    assert [(c.category, c.status, c.entries) for c in page.counts] == [("B", "proposed", 2), ("C", "rejected", 1)]
    assert data_resolve_list(ResolveListRequest(status="rejected"), Context(home)).total == 1


def test_the_commands_approve_a_group_and_list_it(home, capsys):
    note = "user 2023-12-02: class B per the records"
    with pytest.raises(SystemExit) as exited:
        main(["data", "resolve", "approve", "--category", "B", "--note", note, "--json", "--home", str(home)])
    assert exited.value.code == 0 and Decision.model_validate_json(capsys.readouterr().out).changed == 2
    with pytest.raises(SystemExit):
        main(["data", "resolve", "list", "--status", "approved", "--json", "--home", str(home)])
    rows = ResolvePage.model_validate_json(capsys.readouterr().out).rows
    assert [(r.category, r.approval) for r in rows] == [("B", note), ("B", note)]
