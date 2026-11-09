import os
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from tests.test_panel import BARS, A, bar, col, commit, install_stub_rules, seed
from wequant.panel import build, load_panel
from wequant.panel.cache import FORMAT, cache_key


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
    assert panel.key.startswith("f1-v2-") and (cache / "panel" / panel.key / "close.npy").exists()


def test_a_panel_keeps_serving_its_version(store, cache):
    old = load_panel(store=store, cache_dir=cache)
    commit(store, bars=[{**r, "volume": 700} for r in BARS])
    assert (old.raw("volume")[old.traded] == 100.0).all()
    new = load_panel(store=store, cache_dir=cache)
    assert (new.raw("volume")[new.traded] == 700.0).all()
    assert load_panel(version=1, store=store, cache_dir=cache).raw("volume")[0, 0] == 100.0


def test_a_removed_manifest_raises_lookup_error(store, cache):
    commit(store, bars=BARS[1:])
    (store.home / "manifests" / "manifest.v1.json").unlink()
    with pytest.raises(LookupError):
        load_panel(version=1, store=store, cache_dir=cache)
    assert load_panel(version=2, store=store, cache_dir=cache).version == 2


def test_stores_sharing_a_cache_get_their_own_directories(tmp_path, cache):
    one = load_panel(store=seed(tmp_path / "one"), cache_dir=cache)
    two = load_panel(store=seed(tmp_path / "two", bars=BARS[1:]), cache_dir=cache)
    assert one.version == two.version == 1 and one.key != two.key
    assert len(directories(cache)) == 2


def test_versions_the_store_no_longer_keeps_are_pruned(store, cache):
    (cache / "panel" / f"f{FORMAT + 1}-v1-0123456789ab").mkdir(parents=True)
    (cache / "panel" / "notes").mkdir()
    first = load_panel(store=store, cache_dir=cache)
    assert directories(cache) == sorted([first.key, "notes"])
    for n in range(2, 5):
        commit(store, bars=BARS[n:])
    load_panel(version=2, store=store, cache_dir=cache)
    assert directories(cache) == sorted([first.key, cache_key(store.manifest(2)), "notes"])
    latest = load_panel(store=store, cache_dir=cache)
    assert directories(cache) == sorted([cache_key(store.manifest(2)), latest.key, "notes"])


def test_directories_carry_the_format_and_adjusted_prices_are_never_stored(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    for field in ("open", "high", "low", "close", "preclose"):
        panel.adjusted(field)
    names = sorted(p.name for p in (cache / "panel" / panel.key).iterdir())
    assert panel.key.startswith(f"f{FORMAT}-") and directories(cache) == [panel.key]
    assert not [n for n in names if n.startswith("adjusted")]
    assert "factor.npy" in names and "close.npy" in names


@pytest.mark.parametrize("change", ["format", "book"])
def test_a_rules_change_rebuilds_only_the_limits(store, cache, builds, monkeypatch, change):
    calls = install_stub_rules(monkeypatch)
    panel = load_panel(store=store, cache_dir=cache)
    locked = panel.limit_locked
    builds.clear()
    if change == "format":
        monkeypatch.setattr("wequant.panel.panel.LIMITS_FORMAT", 2)
    else:
        monkeypatch.setattr("wequant.panel.panel.default_book", lambda: SimpleNamespace(digest="next" * 16))
    again = load_panel(store=store, cache_dir=cache)
    assert np.array_equal(again.limit_locked, locked) and locked.any()
    assert builds == [] and calls == [1, 1]
    expected = "limits-stubstubstub-r2.npy" if change == "format" else "limits-nextnextnext-r1.npy"
    assert (cache / "panel" / panel.key / expected).exists()


def test_a_corrupt_file_raises_and_stays(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    path = cache / "panel" / panel.key / "close.npy"
    path.write_bytes(b"not an array")
    with pytest.raises(ValueError):
        load_panel(store=store, cache_dir=cache).raw("close")
    assert path.read_bytes() == b"not an array"


def test_the_default_cache_lives_under_the_store_home(store):
    panel = load_panel(store=store)
    assert (store.home / "cache" / "panel" / panel.key / "dates.npy").exists()
