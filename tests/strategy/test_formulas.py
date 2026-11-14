"""S4.1: a strategy keeps each formula's text as written; its hash and its runs' identity follow the compiled tree."""

import json

import pytest

from tests.backtest.market import build_store, mixed_market
from wequant.backtest import Backtest
from wequant.backtest.backtest import panel_for
from wequant.backtest.engine import planned_id
from wequant.strategy import Strategy
from wequant.strategy.library import Library, read_warnings, strategy_hash

WRITTEN = "N:=5;\n{收盘站上均线}\nXG: CLOSE > MA(CLOSE, N);"
RENAMED = "LEN := 5 ; { 改了注释和名字 }\nSIGNAL:C>MA(C,LEN)"  # the same tree: other comments, blanks, names


def strategy(text: str, period: str = "week") -> Strategy:
    return Strategy.from_form({"filters": [{"name": "up", "expr": {"text": text, "period": period}}]})


def test_comments_blanks_and_names_leave_the_hash_and_the_run_id_alone(tmp_path):
    a, b = strategy(WRITTEN), strategy(RENAMED)
    days, bars = mixed_market(30)
    panel = panel_for(Backtest(a), store=build_store(tmp_path / "home", days, bars), cache_dir=tmp_path / "cache")
    assert a.key != b.key and strategy_hash(a) == strategy_hash(b)
    assert planned_id(Backtest(a, benchmark=None), panel) == planned_id(Backtest(b, benchmark=None), panel)


def test_a_strategy_hashes_as_the_same_strategy_written_as_a_tree():
    tree = Strategy.from_form(
        {"filters": [{"name": "up", "expr": ["WEEK", ["GT", "adj_close", ["MA", "adj_close", 5]]]}]}
    )
    assert strategy_hash(strategy(WRITTEN)) == strategy_hash(tree)


def test_the_text_is_the_source_and_a_tree_given_with_it_is_ignored():
    stale = {"text": WRITTEN, "period": "week", "tree": ["GT", "close", 1.0]}
    found = Strategy.from_form({"filters": [{"name": "up", "expr": stale}]})
    assert found.form["filters"][0]["expr"] == {**stale, "tree": ["WEEK", ["GT", "adj_close", ["MA", "adj_close", 5]]]}


def test_a_saved_strategy_keeps_each_version_as_written(tmp_path):
    """Changing only a comment saves a new version, with the hash of the one before, that reads back as written."""
    library = Library(tmp_path / "strategies")
    library.save(strategy(WRITTEN), "s")
    entry, created = library.save(strategy(RENAMED), "s")
    assert created and [v.n for v in entry.versions] == [1, 2]
    assert entry.versions[0].strategy_hash == entry.versions[1].strategy_hash
    assert [library.load("s", n)[2].form["filters"][0]["expr"]["text"] for n in (1, 2)] == [WRITTEN, RENAMED]
    assert not library.save(strategy(RENAMED), "s")[1]  # the same text again is no new version


def test_a_formula_compiling_to_another_tree_than_when_saved_is_read_with_a_warning(tmp_path):
    """The text stays the source: a version whose stored tree is not what its text compiles to now reads as the text,
    with formula_recompiled naming both hashes."""
    library = Library(tmp_path / "strategies")
    library.save(strategy(WRITTEN), "s")
    path = library.root / "s" / "1.json"
    form = json.loads(path.read_text("utf-8"))
    form["filters"][0]["expr"]["tree"] = ["WEEK", ["GT", "adj_close", ["MA", "adj_close", 6]]]  # as an older compiler
    path.write_text(json.dumps(form, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    (library.root / "s" / "meta.json").unlink()  # the index rebuilt from the file, as a version saved like this was
    entry, version, found = library.load("s")
    warned = read_warnings(entry, version, found)
    assert "formula_recompiled" in warned and warned["formula_recompiled"][1]["now"] == strategy_hash(strategy(WRITTEN))


@pytest.mark.parametrize(
    ("key", "value", "code"), [("color", "red", "unknown_key"), ("period", "hour", "not_a_choice")]
)
def test_a_formula_object_has_only_its_fields(key, value, code):
    from wequant.strategy import SpecErrors

    with pytest.raises(SpecErrors) as refused:
        Strategy.from_form({"filters": [{"name": "up", "expr": {"text": "C>O", key: value}}]})
    assert [e.code for e in refused.value.errors] == [code]
