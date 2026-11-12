"""What cleanup keeps: the newest keep_versions data versions plus the pinned ones, the one in use and those pinned runs
read; runs until the archive is over its limit, the oldest unpinned going first; the panels of the version in use."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from wequant.data.store import Store
from wequant.panel.cache import keep_only
from wequant.runs.archive import Archive
from wequant.storage.settings import Settings

GB = 1 << 30


def protected(archive: Archive, settings: Settings) -> set[int]:
    """Versions every cleanup keeps: pinned, in use, and read by a pinned run that is archived."""
    read = {archive.row(run)["store_version"] for run in settings.pinned_runs if run in archive}
    in_use = {settings.version_in_use} if settings.version_in_use is not None else set()
    return {*settings.pinned_versions, *in_use, *read}


def clean_versions(store: Store, archive: Archive, settings: Settings, dry_run: bool = False) -> list[Path]:
    """The files and manifests only versions cleanup does not keep need; deleted unless dry_run."""
    keep, protect = settings.keep_versions, protected(archive, settings)
    return store.cleanup(keep=keep, dry_run=dry_run, protect=protect)


def runs_over(archive: Archive, settings: Settings, keep: Iterable[str] = ()) -> list[tuple[str, int]]:
    """(id, bytes) of the oldest runs to remove, pinned ones and keep never among them, until the archive fits its
    limit."""
    sizes = {run: archive.size(run) for run in archive.index()["id"]}
    total, limit, spared = sum(sizes.values()), settings.runs_limit_gb * GB, {*settings.pinned_runs, *keep}
    doomed = []
    for run in sizes:  # the index is oldest first
        if total <= limit:
            break
        if run not in spared:
            doomed.append((run, sizes[run]))
            total -= sizes[run]
    return doomed


def prune_runs(archive: Archive, settings: Settings, keep: Iterable[str] = ()) -> list[str]:
    """Remove runs_over; the removed ids."""
    doomed = [run for run, _ in runs_over(archive, settings, keep)]
    for run in doomed:
        archive.delete(run)
    return doomed


def prune_panels(cache: Path, store: Store, settings: Settings) -> list[Path]:
    """Remove the panels of every version but the one in use (the newest when none is set)."""
    in_use = store.manifest(settings.version_in_use) or store.manifest()
    return keep_only(cache, in_use) if in_use is not None else []
