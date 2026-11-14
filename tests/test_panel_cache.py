import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from tests.test_panel import BARS, A, bar, col, commit, install_stub_rules, seed
from wequant.data.files import FileInUse
from wequant.data.store import KEEP_VERSIONS
from wequant.panel import build, load_panel
from wequant.panel.cache import FORMAT, PanelCache, cache_key, keep_only


@pytest.fixture
def store(tmp_path):
    return seed(tmp_path / "home")


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def builds(monkeypatch):
    """Names of the arrays built through BUILDERS, in order."""
    seen = []
    for name, builder in list(build.BUILDERS.items()):
        monkeypatch.setitem(build.BUILDERS, name, _spy(name, builder, seen))
    return seen


def _spy(name, builder, seen):
    def spied(src):
        seen.append(name)
        return builder(src)

    return spied


@pytest.fixture
def reads(monkeypatch):
    """Names of the Parquet files read through polars."""
    seen = []
    real = pl.scan_parquet

    def scan(source, *args, **kwargs):
        seen.extend(os.path.basename(str(p)) for p in (source if isinstance(source, list) else [source]))
        return real(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", scan)
    return seen


def directories(cache):
    return sorted(p.name for p in (cache / "panel").iterdir())


def test_first_use_builds_once_then_serves_from_disk(store, cache, builds, reads):
    load_panel(store=store, cache_dir=cache).raw("close")
    assert sorted(builds) == ["close", "codes", "dates"]
    builds.clear()
    reads.clear()
    close = load_panel(store=store, cache_dir=cache).raw("close")
    assert builds == [] and reads == [] and close[1, 2] == 10.0


def test_a_new_version_is_served_from_its_own_directory(store, cache):
    first = load_panel(store=store, cache_dir=cache)
    assert first.raw("close")[1, col(first, A)] == 10.0
    commit(store, bars=[bar(A, 2, 10100, 9800) if (r["code"], r["date"]) == (A, "2024-01-03") else r for r in BARS])
    panel = load_panel(store=store, cache_dir=cache)
    assert panel.raw("close")[1, col(panel, A)] == 10.1
    assert panel.key.startswith("f2-v2-") and (cache / "panel" / panel.key / "close.npy").exists()
    assert first.raw("open")[1, col(first, A)] == 10.0  # the v1 panel keeps serving v1, even a field read only now
    assert load_panel(version=1, store=store, cache_dir=cache).raw("close")[1, col(first, A)] == 10.0


def test_stores_sharing_a_cache_get_their_own_directories(tmp_path, cache):
    one = load_panel(store=seed(tmp_path / "one"), cache_dir=cache)
    two = load_panel(store=seed(tmp_path / "two", bars=BARS[1:]), cache_dir=cache)
    assert one.version == two.version == 1 and one.key != two.key
    assert len(directories(cache)) == 2


def test_versions_the_store_no_longer_keeps_are_pruned(store, cache):
    (cache / "panel" / f"f{FORMAT + 1}-v1-0123456789ab").mkdir(parents=True)
    (cache / "panel" / "notes").mkdir()
    first = load_panel(store=store, cache_dir=cache).key  # no panel of version 1 stays open (Windows keeps a
    for n in range(2, KEEP_VERSIONS + 2):  # mapped folder); versions 2 .. KEEP_VERSIONS + 1: version 1 falls out
        commit(store, bars=BARS[n:])
    load_panel(version=2, store=store, cache_dir=cache)
    assert directories(cache) == sorted([first, cache_key(store.manifest(2)), "notes"])
    latest = load_panel(store=store, cache_dir=cache)
    assert directories(cache) == sorted([cache_key(store.manifest(2)), latest.key, "notes"])


@pytest.mark.parametrize("change", ["book"])
def test_a_rules_change_rebuilds_only_the_limits(store, cache, builds, monkeypatch, change):
    calls = install_stub_rules(monkeypatch)
    panel = load_panel(store=store, cache_dir=cache)
    locked = panel.limit_locked
    builds.clear()
    monkeypatch.setattr("wequant.panel.panel.default_book", lambda: SimpleNamespace(digest="next" * 16))
    again = load_panel(store=store, cache_dir=cache)
    assert np.array_equal(again.limit_locked, locked) and locked.any()
    assert builds == [] and calls == [1, 1]
    assert (cache / "panel" / panel.key / "limits-nextnextnext-r1.npy").exists()


def test_a_cleanup_during_a_panel_build_neither_fails_it_nor_removes_the_folder_being_built(store, cache, monkeypatch):
    """The race the frontend saw: switching the version in use prunes the other panels at the moment a request moves
    its new array into place. The build still returns its array, and its folder is kept."""
    commit(store, bars=BARS[1:])  # version 2: the newest; version 1's panel is the one being built
    building = PanelCache(store, store.manifest(1), cache)
    replace = os.replace

    def raced(source, target):
        keep_only(cache, store.manifest(2))  # what data_use_version's tidy does, from another request
        replace(source, target)

    monkeypatch.setattr(os, "replace", raced)
    array = building.array("probe", lambda src: np.arange(3.0))
    assert array.tolist() == [0.0, 1.0, 2.0] and (building.directory / "probe.npy").is_file()


def test_a_panel_folder_another_process_removes_mid_write_still_gives_the_array(store, cache, monkeypatch):
    building = PanelCache(store, store.manifest(), cache)

    def lost(source, target):  # the folder went with its temporary file, as another process's cleanup would do
        raise FileNotFoundError(source)

    monkeypatch.setattr(os, "replace", lost)
    assert building.array("probe", lambda src: np.arange(3.0)).tolist() == [0.0, 1.0, 2.0]


def test_two_threads_missing_one_array_build_it_once_and_both_read_it(store, cache):
    """The second waits for the first's file instead of writing over it: Windows refuses to replace a file another
    thread has mapped, which answered 500 in the end-to-end runs."""
    built, start = [], threading.Barrier(2)

    def slow(src):
        built.append(1)
        time.sleep(0.2)
        return np.arange(3.0)

    def ask():
        start.wait()
        return PanelCache(store, store.manifest(), cache).array("probe", slow).tolist()

    with ThreadPoolExecutor(2) as pool:
        got = list(pool.map(lambda _: ask(), range(2)))
    assert got == [[0.0, 1.0, 2.0]] * 2 and built == [1]


def test_a_file_another_process_holds_through_the_replace_is_read_as_it_stands(store, cache, monkeypatch):
    """The other process wrote the same array first and maps it: its copy serves."""
    building = PanelCache(store, store.manifest(), cache)
    building.array("probe", lambda src: np.arange(3.0))
    (cache / "panel" / building.key / "probe.npy").rename(cache / "kept.npy")

    def held(source, target):  # the other process writes the file and maps it as this one replaces it
        (cache / "kept.npy").rename(target)
        raise FileInUse(target)

    monkeypatch.setattr("wequant.panel.cache.atomic_write", lambda path, data: held(None, path))
    assert building.array("probe", lambda src: np.zeros(3)).tolist() == [0.0, 1.0, 2.0]
