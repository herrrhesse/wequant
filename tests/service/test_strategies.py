"""The strategy operations over a data root's saved strategies."""

import json

import pytest

from tests.service.conftest import MOM
from wequant.data.store import exclusive
from wequant.service import (
    ArchiveRequest,
    Context,
    Exit,
    SaveRequest,
    ServiceError,
    ShowRequest,
    StrategyListRequest,
    StrategyRequest,
    strategy_archive,
    strategy_list,
    strategy_save,
    strategy_show,
    strategy_validate,
)
from wequant.service.specs import StrategySpec


def test_saved_as_lists_the_versions_with_the_same_content(library_home):
    context = Context(library_home)
    saved = strategy_validate(StrategyRequest(strategy=StrategySpec.model_validate(MOM)), context)
    unsaved = strategy_validate(StrategyRequest(strategy=StrategySpec.model_validate({**MOM, "top_n": 9})), context)
    assert [(r.id, r.n) for r in saved.saved_as] == [("s", 1)] and unsaved.saved_as == []


def test_a_saved_version_shows_the_content_it_was_saved_with(library_home):
    context = Context(library_home)
    spec = StrategySpec.model_validate({**MOM, "period": "month"})
    version = strategy_save(SaveRequest(strategy=spec, id="s", note="monthly"), context)
    shown = strategy_show(ShowRequest(id="s", n=version.n), context)
    assert (version.n, version.created, shown.strategy, shown.note) == (3, True, spec, "monthly")
    assert shown.strategy_hash == strategy_validate(StrategyRequest(strategy=spec), context).strategy_hash


def test_reading_a_saved_strategy_passes_on_the_librarys_warnings(library_home):
    meta = library_home / "strategies" / "s" / "meta.json"
    index = json.loads(meta.read_text())
    meta.write_text(json.dumps({**index, "versions": index["versions"][:1]}))
    checked = strategy_validate(StrategyRequest(saved={"id": "s"}), Context(library_home))
    assert [(w.code, w.params) for w in checked.warnings] == [("meta_rebuilt", {"versions": 1})]


FAILURES = [
    (lambda c: strategy_show(ShowRequest(id="nope"), c), Exit.NOT_FOUND, "unknown_strategy", ["id"]),
    (lambda c: strategy_show(ShowRequest(id="s", n=9), c), Exit.NOT_FOUND, "unknown_version", ["n"]),
    (
        lambda c: strategy_validate(StrategyRequest(saved={"id": "nope"}), c),
        Exit.NOT_FOUND,
        "unknown_strategy",
        ["saved", "id"],
    ),
    (
        lambda c: strategy_save(SaveRequest(strategy=StrategySpec(), id="Bad"), c),
        Exit.USAGE,
        "bad_ref",
        ["id"],
    ),
]


@pytest.mark.parametrize("call, exit, code, loc", FAILURES, ids=[f"{case[2]} {case[3]}" for case in FAILURES])
def test_the_librarys_failures_are_located(library_home, call, exit, code, loc):
    with pytest.raises(ServiceError) as raised:
        call(Context(library_home))
    assert raised.value.exit == exit and [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_a_held_strategy_lock_is_locked(library_home):
    with exclusive(library_home / "strategies" / "s" / ".lock"), pytest.raises(ServiceError) as raised:
        strategy_archive(ArchiveRequest(id="s"), Context(library_home))
    assert raised.value.exit == Exit.LOCKED


def test_the_list_leaves_out_archived_strategies_unless_asked(library_home):
    context = Context(library_home)
    strategy_save(SaveRequest(strategy=StrategySpec(), id="t"), context)
    strategy_archive(ArchiveRequest(id="s"), context)
    assert [r.id for r in strategy_list(StrategyListRequest(), context).rows] == ["t"]
    assert [r.id for r in strategy_list(StrategyListRequest(archived=True), context).rows] == ["s", "t"]
