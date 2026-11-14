"""Browsing folders for a data root (round 3, F-30): folders only, data roots marked, new folders checked."""

import pytest

from tests.test_panel import seed
from wequant.storage.folders import make, places, subfolders
from wequant.storage.relocate import Refused, files_of


def test_a_listing_shows_visible_subfolders_only_and_marks_data_roots(tmp_path):
    seed(tmp_path / "data")
    (tmp_path / "plain").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "notes.txt").write_text("a file is never listed", encoding="utf-8")
    found = {f.name: (f.is_data_root, f.size_bytes) for f in subfolders(tmp_path)}
    size = sum((tmp_path / "data" / f).stat().st_size for f in files_of(tmp_path / "data"))
    assert found == {"data": (True, size), "plain": (False, None)}


@pytest.mark.parametrize("name, code", [("", "bad_name"), ("a/b", "bad_name"), ("a:b", "bad_name"), ("x", "exists")])
def test_a_new_folder_needs_a_usable_new_name(tmp_path, name, code):
    (tmp_path / "x").mkdir()
    with pytest.raises(Refused) as refused:
        make(tmp_path, name)
    assert refused.value.code == code


def test_browsing_starts_at_the_home_folder():
    assert places()[0].kind == "home"
