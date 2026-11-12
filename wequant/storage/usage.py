"""How much each part of a data root takes, and how much cleanup would free."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wequant.data.store import Store, version_files
from wequant.panel.cache import cache_key
from wequant.runs.archive import Archive
from wequant.storage.retention import clean_versions, runs_over
from wequant.storage.settings import Settings


@dataclass(frozen=True)
class Usage:
    """Bytes on disk."""

    current: int  # the data the newest version and the one in use read
    versions: int  # files only other kept versions read
    cache: int  # panels
    runs: int  # the run archive
    free_versions: int  # what cleaning versions frees
    free_cache: int  # panels of versions other than the one in use
    free_runs: int  # runs beyond the archive's limit


def usage(store: Store, archive: Archive, cache: Path, settings: Settings) -> Usage:
    """The data root's usage under its settings."""
    newest = store.manifest()
    in_use = store.manifest(settings.version_in_use) or newest
    current = {e["path"] for manifest in (newest, in_use) if manifest for e in version_files(manifest)}
    kept = {e["path"] for v in store.versions() for e in version_files(store.manifest(v))}
    panels = [p for p in (Path(cache) / "panel").glob("*") if p.is_dir()]
    key = cache_key(in_use) if in_use else None
    return Usage(
        current=_bytes(store.home / path for path in current),
        versions=_bytes(store.home / path for path in kept - current),
        cache=sum(_tree(p) for p in panels),
        runs=sum(archive.size(run) for run in archive.index()["id"]),
        free_versions=_bytes(clean_versions(store, archive, settings, dry_run=True)),
        free_cache=sum(_tree(p) for p in panels if p.name != key),
        free_runs=sum(size for _, size in runs_over(archive, settings)),
    )


def _bytes(paths) -> int:
    return sum(path.stat().st_size for path in paths if path.is_file())


def _tree(directory: Path) -> int:
    return _bytes(directory.rglob("*"))
