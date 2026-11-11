"""Saved strategies (D16): write-once version files, the index rebuilt from them, and the content hash."""

import datetime as dt
import hashlib
import json
import re
from dataclasses import fields, replace

import pytest

from wequant.data.store import StoreLocked, exclusive
from wequant.expr import Field
from wequant.strategy import Condition, Factor, Pool, SpecError, Strategy
from wequant.strategy.library import Library, NotSaved, read_warnings, strategy_hash

A = Strategy(factors=(Factor("a", Field("close")),), top_n=5)
B = replace(A, top_n=6)


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "strategies")


def meta(library, id):
    return json.loads((library.root / id / "meta.json").read_text())


def test_the_hash_is_the_sha256_of_the_whole_canonical_text():
    """D16: versions and runs are matched by the first 16 hex digits of the sha256 of the strategy's canonical text,
    every field in it (each one set here)."""
    every = Strategy(
        universe=~Field("exchange@BJ") & (Field("close") > 1.0),
        filters=(Condition("up", Field("close") > Field("open")),),
        factors=(Factor("a", Field("close"), prefer="low", weight=2.0),),
        top_n=5,
        backups=1,
        period="month",
        every=2,
        weighting="formula",
        formula=Field("amount"),
        band=0.1,
        anchor=dt.date(2016, 11, 7),
        hold_rank=8,
        max_weight=0.3,
        pool=Pool(index="000300.SH", boards=("main",), exclude_st=True, min_list_days=60),
    )
    assert strategy_hash(every) == hashlib.sha256(every.key.encode()).hexdigest()[:16]
    assert {f.name for f in fields(Strategy)} == set(json.loads(every.key))  # no field left at its default


def test_unchanged_content_adds_no_version(library):
    library.save(A, "s")
    entry, created = library.save(A, "s", name="renamed")
    assert not created and [v.n for v in entry.versions] == [1] and meta(library, "s")["name"] == "renamed"


def test_new_content_is_the_next_version_and_older_files_stay(library):
    library.save(A, "s")
    first = (library.root / "s" / "1.json").read_bytes()
    entry, created = library.save(B, "s", note="wider")
    assert created and [(v.n, v.note) for v in entry.versions] == [(1, None), (2, "wider")]
    assert (library.root / "s" / "1.json").read_bytes() == first == A.key.encode() + b"\n"


def test_restoring_old_content_is_a_new_version_with_the_old_hash(library):
    library.save(A, "s")
    library.save(B, "s")
    entry, created = library.save(A, "s")
    assert created and [v.strategy_hash for v in entry.versions] == [strategy_hash(s) for s in (A, B, A)]


def test_a_version_file_missing_from_the_index_is_read_and_written_back_only_by_a_save(library):
    library.save(A, "s")
    library.save(B, "s")
    index = meta(library, "s")
    (library.root / "s" / "meta.json").write_text(json.dumps({**index, "versions": index["versions"][:1]}))
    entry = library.entry("s")
    assert entry.rebuilt == 1 and len(meta(library, "s")["versions"]) == 1  # reading writes nothing
    assert (entry.current.n, entry.current.strategy_version, entry.current.note) == (2, None, None)
    assert entry.current.strategy_hash == strategy_hash(B)
    library.save(B, "s")
    assert library.entry("s").rebuilt == 0 and len(meta(library, "s")["versions"]) == 2


def test_an_index_entry_without_its_file_is_dropped(library):
    library.save(A, "s")
    library.save(B, "s")
    (library.root / "s" / "2.json").unlink()
    entry = library.entry("s")
    assert [v.n for v in entry.versions] == [1] and entry.rebuilt == 1


def test_a_version_file_changed_after_saving_is_refused(library):
    library.save(A, "s")
    (library.root / "s" / "1.json").write_text(B.key + "\n")
    with pytest.raises(SpecError) as raised:
        library.load("s", 1)
    assert raised.value.code == "hash_mismatch"


@pytest.mark.parametrize("id", ["../x", "a" * 41], ids=["path_traversal", "41_characters"])
def test_a_malformed_id_is_refused_before_any_file_is_touched(library, id):
    with pytest.raises(SpecError) as raised:
        library.save(A, id)
    assert raised.value.code == "bad_ref" and not library.root.exists()


def test_a_new_strategy_without_an_id_gets_a_generated_one(library):
    entry, _ = library.save(A)
    assert re.fullmatch(r"s-[0-9a-f]{8}", entry.id) and entry.name == entry.id


@pytest.mark.parametrize("n, code", [(None, "unknown_strategy"), (3, "unknown_version")])
def test_what_is_not_saved(library, n, code):
    library.save(A, "s")
    with pytest.raises(NotSaved) as raised:
        library.load("nope" if n is None else "s", n)
    assert raised.value.code == code


def test_a_save_needs_the_strategy_lock(library):
    library.save(A, "s")
    with exclusive(library.root / "s" / ".lock"), pytest.raises(StoreLocked):
        library.save(B, "s")
    assert [v.n for v in library.entry("s").versions] == [1]


def test_archiving_only_flags_the_strategy(library):
    library.save(A, "s")
    before = library.entry("s")
    assert library.archive("s") == replace(before, archived=True) == library.entry("s")
    assert library.archive("s", restore=True) == before


def test_saved_as_lists_every_version_with_the_content(library):
    library.save(A, "s")
    library.save(B, "s")
    library.save(A, "t")
    library.archive("t")
    assert library.saved_as(strategy_hash(A)) == [("s", 1), ("t", 1)]


def test_reading_warns_of_another_strategy_version_and_of_a_rebuilt_index(library):
    library.save(A, "s")
    entry = library.entry("s")
    assert read_warnings(entry, entry.current) == {}
    old = replace(entry.current, strategy_version=entry.current.strategy_version - 1)
    found = read_warnings(replace(entry, rebuilt=2), old)
    assert list(found) == ["strategy_version_changed", "meta_rebuilt"]
    assert found["strategy_version_changed"][1] == {"saved": old.strategy_version, "current": old.strategy_version + 1}
    assert found["meta_rebuilt"][1] == {"versions": 2}


def test_a_save_is_stamped_by_the_library_s_clock(tmp_path):
    library = Library(tmp_path / "strategies", clock=lambda: "2026-01-01T00:00:00+00:00")
    library.save(A, "a")
    entry = library.entry("a")
    assert (entry.created_at, entry.versions[0].saved_at) == ("2026-01-01T00:00:00+00:00",) * 2
