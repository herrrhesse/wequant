"""Moving and copying a data root, and telling a complete data root from any folder (round 3, F-30)."""

from collections import namedtuple

import pytest

from tests.test_panel import seed
from wequant.data.store import Store
from wequant.storage import relocate
from wequant.storage.relocate import Cancelled, Refused, check_target, copy_root, files_of, problem


@pytest.fixture
def home(tmp_path):
    """tests.test_panel's store, with a panel cache folder a copy leaves out."""
    found = seed(tmp_path / "home").home
    (found / "cache" / "panel").mkdir(parents=True)
    (found / "cache" / "panel" / "x.npy").write_bytes(b"rebuilt, never copied")
    return found


def test_a_copy_has_every_file_but_the_cache_and_reads_as_the_same_version(home, tmp_path):
    target = tmp_path / "copy"
    copied = copy_root(home, target, move=False, progress=lambda done, total: None, cancel=lambda: False)
    assert sorted(files_of(target)) == sorted(files_of(home)) and not (target / "cache").exists()
    assert Store(target).manifest() == Store(home).manifest() and (copied.files, copied.renamed) == (
        len(files_of(home)),
        False,
    )


def test_a_move_on_one_disk_renames_the_folder(home, tmp_path):
    manifest = Store(home).manifest()
    copied = copy_root(home, tmp_path / "moved", move=True, progress=lambda done, total: None, cancel=lambda: False)
    assert copied.renamed and not home.exists() and Store(tmp_path / "moved").manifest() == manifest


@pytest.mark.parametrize("existing", [False, True], ids=["new folder", "empty folder"])
def test_a_cancelled_copy_leaves_the_source_as_it_was_and_nothing_at_the_target(home, tmp_path, monkeypatch, existing):
    """The cancel comes after the first chunk has been written."""
    target, before = tmp_path / "copy", {f: (home / f).read_bytes() for f in files_of(home)}
    if existing:
        target.mkdir()
    monkeypatch.setattr(relocate, "CHUNK", 64)
    asked = iter([False, True])
    with pytest.raises(Cancelled):
        copy_root(home, target, move=False, progress=lambda done, total: None, cancel=lambda: next(asked, True))
    assert {f: (home / f).read_bytes() for f in files_of(home)} == before
    assert (list(target.iterdir()) == []) if existing else not target.exists()


Usage = namedtuple("Usage", "total used free")


def full(home, tmp):
    (tmp / "full").mkdir()
    (tmp / "full" / "a").write_text("a", encoding="utf-8")
    return tmp / "full"


REFUSALS = [
    ("not_empty", full),
    ("inside", lambda home, tmp: home / "sub"),
    ("no_space", lambda home, tmp: tmp / "small"),
]


@pytest.mark.parametrize("code, target", REFUSALS, ids=[case[0] for case in REFUSALS])
def test_a_target_that_cannot_take_the_data_is_refused(home, tmp_path, monkeypatch, code, target):
    monkeypatch.setattr(relocate.shutil, "disk_usage", lambda path: Usage(10**6, 10**6 - 100, 100))
    with pytest.raises(Refused) as refused:
        check_target(home, target(home, tmp_path), size=1000 if code == "no_space" else 0)
    assert refused.value.code == code


def test_only_a_folder_with_complete_data_is_a_data_root(home, tmp_path):
    (tmp_path / "empty").mkdir()
    entry = next(iter(Store(home).manifest()["tables"].values()))["files"][0]["path"]
    incomplete = tmp_path / "incomplete"
    copy_root(home, incomplete, move=False, progress=lambda done, total: None, cancel=lambda: False)
    (incomplete / entry).unlink()
    found = [problem(path) for path in (tmp_path / "missing", tmp_path / "empty", incomplete, home)]
    assert [p and p.code for p in found] == ["no_directory", "not_a_data_root", "data_root_incomplete", None]
