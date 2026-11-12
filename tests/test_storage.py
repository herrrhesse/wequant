"""Storage management of a data root (round 2): settings, what cleanup keeps, and runs, versions and strategies as
files. Each test names its rule; synthetic data only."""

import json

from tests.service.conftest import market_home  # noqa: F401  a market with strategy m saved
from wequant import service
from wequant.data.store import Store, version_files, version_reason
from wequant.panel.cache import cache_key
from wequant.runs.archive import RUNS, Archive
from wequant.service import Context
from wequant.storage.files import (
    export_run,
    export_strategy,
    export_version,
    import_run,
    import_strategy,
    import_version,
)
from wequant.storage.retention import clean_versions, prune_panels, prune_runs
from wequant.storage.settings import Settings, change, load

META = {"last_trade_date": "2024-01-05"}


def versions(store: Store, n: int) -> None:
    """n more versions, each changing meta only."""
    for i in range(n):
        store.commit({**META, "note": f"{len(store.versions())}-{i}"})


def backtests(home, n: int) -> list[str]:
    """Run ids of n archived backtests of strategy m with different cash, oldest first."""
    request = lambda cash: service.BacktestRequest(saved={"id": "m"}, cash=cash, benchmark=None)  # noqa: E731
    return [service.backtest(request(1_000_000.0 + i), Context(home)).run_id for i in range(n)]


def test_without_a_settings_file_every_setting_is_its_default(tmp_path):
    assert load(tmp_path) == (Settings(), [])


def test_a_setting_that_is_not_valid_reads_as_its_default_and_says_why(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"keep_versions": 0, "runs_limit_gb": 1.5, "colour": "red"}))
    settings, problems = load(tmp_path)
    assert (settings.keep_versions, settings.runs_limit_gb, len(problems)) == (5, 1.5, 2)


def test_a_change_is_written_and_read_back(tmp_path):
    change(tmp_path, lambda s: {"version_in_use": 3, "pinned_runs": ("0123456789abcdef",)})
    assert load(tmp_path)[0] == Settings(version_in_use=3, pinned_runs=("0123456789abcdef",))


def test_cleanup_keeps_the_newest_the_pinned_the_one_in_use_and_those_pinned_runs_read(market_home):  # noqa: F811
    store, archive = Store(market_home), Archive(market_home / RUNS)
    run = backtests(market_home, 1)[0]  # reads version 1
    versions(store, 7)  # versions 2..8
    settings = Settings(keep_versions=2, pinned_versions=(3,), version_in_use=5, pinned_runs=(run,))
    clean_versions(store, archive, settings)
    assert store.versions() == [1, 3, 5, 7, 8]


def test_the_archive_over_its_limit_loses_its_oldest_unpinned_runs(market_home):  # noqa: F811
    archive = Archive(market_home / RUNS)
    oldest, pinned, middle, newest = backtests(market_home, 4)
    settings = Settings(runs_limit_gb=1e-12, pinned_runs=(pinned,))  # every run is over it
    assert prune_runs(archive, settings, keep=[newest]) == [oldest, middle]
    assert sorted(archive.index()["id"]) == sorted([pinned, newest])


def panels(cache, store: Store) -> list[str]:
    """A panel folder in cache for each of the store's versions; their names."""
    keys = [cache_key(store.manifest(v)) for v in store.versions()]
    for key in keys:
        (cache / "panel" / key).mkdir(parents=True)
    return keys


def test_only_the_panels_of_the_version_in_use_stay(market_home):  # noqa: F811
    store, cache = Store(market_home), market_home / "cache"
    versions(store, 2)
    keys = panels(cache, store)
    prune_panels(cache, store, Settings(version_in_use=2))
    assert [p.name for p in (cache / "panel").iterdir()] == [keys[1]]


def test_panels_outside_the_data_root_are_never_pruned(market_home, tmp_path):  # noqa: F811
    store, cache = Store(market_home), tmp_path / "elsewhere"  # another store's cache, given with --cache
    versions(store, 2)
    keys = panels(cache, store)
    assert prune_panels(cache, store, Settings()) == [] and sorted(
        p.name for p in (cache / "panel").iterdir()
    ) == sorted(keys)


def test_a_run_exported_and_imported_elsewhere_is_byte_for_byte_the_same(market_home, tmp_path):  # noqa: F811
    run = backtests(market_home, 1)[0]
    source, target = Archive(market_home / RUNS), Archive(tmp_path / "elsewhere" / RUNS)
    export_run(source, run, tmp_path / "a.wqrun")
    assert import_run(target, tmp_path / "a.wqrun") == (run, True)
    assert target.files(run) == source.files(run)
    export_run(target, run, tmp_path / "b.wqrun")
    assert (tmp_path / "a.wqrun").read_bytes() == (tmp_path / "b.wqrun").read_bytes()


def test_a_version_imported_elsewhere_reads_the_same_files_and_is_not_the_current_one(market_home, tmp_path):  # noqa: F811
    source, target = Store(market_home), Store(tmp_path / "elsewhere")
    target.commit(META, replace={"calendar": source.read_table("calendar")})
    export_version(source, 1, tmp_path / "v.wqver")
    added = import_version(target, tmp_path / "v.wqver")
    digests = lambda m: sorted(e["sha256"] for e in version_files(m))  # noqa: E731
    assert (added, target.manifest()["version"], version_reason(target.manifest(added))["code"]) == (
        2,
        1,
        "version_import",
    )
    assert digests(target.manifest(added)) == digests(source.manifest(1))
    assert target.commit({**META, "note": "next"}).version == 3  # above the added version


def test_a_strategy_imported_where_its_name_and_id_are_taken_gets_new_ones(market_home, tmp_path):  # noqa: F811
    library = Context(market_home).library()
    export_strategy(library, "m", tmp_path / "m.wqstrategy")
    entry = import_strategy(library, tmp_path / "m.wqstrategy")
    assert (entry.id != "m", entry.name, library.load(entry.id)[2].form) == (True, "m (2)", library.load("m")[2].form)
