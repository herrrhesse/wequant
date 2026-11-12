"""Storage management (round 2): the data root's settings, the version in use, pins, deleting runs, how much each part
takes, and cleaning up. Cleanup keeps the newest keep_versions versions, the pinned ones, the one in use and those
pinned runs read; runs go oldest first beyond the archive's size limit, pinned ones never; panels of other versions
than the one in use go. Writes that commit a version and settings changes tidy up afterwards."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field

from wequant.data.files import OutsideRoot, check_inside
from wequant.data.store import StoreLocked
from wequant.runs.archive import RUNS, Archive
from wequant.service.base import Context, Model, guarded, manifest, not_found
from wequant.service.specs import Name
from wequant.storage.retention import clean_versions, prune_panels, prune_runs
from wequant.storage.settings import change, load, pinned
from wequant.storage.usage import usage

log = logging.getLogger(__name__)


class SettingsRequest(Model):
    """The data root's settings."""


class SettingsOut(Model):
    """The data root's settings (<home>/settings.json) as they read."""

    runs_limit_gb: float
    """The run archive's size limit; beyond it the oldest unpinned runs go when a run is archived."""
    keep_versions: int
    """Data versions cleanup keeps, besides the pinned ones, the one in use and those pinned runs read."""
    version_in_use: int | None
    """What requests without a data_version read; null: the current version."""
    pinned_versions: list[int]
    pinned_runs: list[str]
    problems: list[str]
    """Why a value of settings.json reads as its default."""


class SettingsUpdate(Model):
    """New limits; a null field keeps its setting."""

    runs_limit_gb: float | None = Field(None, gt=0)
    keep_versions: int | None = Field(None, ge=1)


class UseVersionRequest(Model):
    """The version requests read when they name none."""

    version: int | None = Field(None, ge=1)
    """A kept version; null: the current version again."""


class PinVersionRequest(Model):
    """Pin a data version so cleanup keeps it, or unpin it."""

    version: int = Field(ge=1)
    pinned: bool = True


class PinRunRequest(Model):
    """Pin a run so the archive's size limit never removes it (cleanup then keeps its version), or unpin it."""

    id: Name
    pinned: bool = True


class DeleteRunRequest(Model):
    """Remove one archived run."""

    id: Name


class RunDeleted(Model):
    """The run removed and the bytes it took."""

    id: str
    bytes: int


class UsageRequest(Model):
    """How much each part of the data root takes."""


class StorageUsage(Model):
    """Bytes on disk, the limits, and what cleanup would free."""

    current: int
    """The data the current version and the one in use read."""
    versions: int
    """Files only other kept versions read."""
    cache: int
    """Panels."""
    runs: int
    """The run archive."""
    runs_limit: int
    keep_versions: int
    free_versions: int
    """Files and manifests of versions cleanup does not keep."""
    free_cache: int
    """Panels of versions other than the one in use."""
    free_runs: int
    """The oldest unpinned runs beyond the archive's limit."""
    free: int
    """All of it."""


class CleanupRequest(Model):
    """Clean up now: versions, runs over the limit and panels, as cleanup keeps them."""

    dry_run: bool = False
    """Only count."""


class Cleanup(Model):
    """Usage before and after, and what went."""

    before: StorageUsage
    after: StorageUsage
    files_removed: int
    runs_removed: list[str]
    panels_removed: int


def settings_show(request: SettingsRequest, context: Context = Context()) -> SettingsOut:
    """The data root's settings and why any value reads as its default."""
    settings, problems = load(context.store().home)
    return _out(settings, problems)


def settings_update(request: SettingsUpdate, context: Context = Context()) -> SettingsOut:
    """Set the run archive's limit and how many versions cleanup keeps, then tidy up."""
    given = request.model_dump(exclude_none=True)
    change(context.store().home, lambda settings: given)
    tidy(context)
    return settings_show(SettingsRequest(), context)


def data_use_version(request: UseVersionRequest, context: Context = Context()) -> SettingsOut:
    """Read a kept version when requests name none (null: the current one again); its panels stay, others go."""
    store = context.store()
    if request.version is not None:
        manifest(store, request.version)  # NOT_FOUND unless kept
    change(store.home, lambda settings: {"version_in_use": request.version})
    tidy(context)
    return settings_show(SettingsRequest(), context)


def data_pin_version(request: PinVersionRequest, context: Context = Context()) -> SettingsOut:
    """Pin or unpin a kept version; once unpinned, cleanup may remove it."""
    store = context.store()
    manifest(store, request.version)
    change(store.home, lambda s: {"pinned_versions": pinned(s.pinned_versions, request.version, request.pinned)})
    tidy(context)
    return settings_show(SettingsRequest(), context)


def runs_pin(request: PinRunRequest, context: Context = Context()) -> SettingsOut:
    """Pin or unpin an archived run."""
    home = context.store().home
    if request.id not in Archive(home / RUNS):
        raise not_found("unknown_run", f"no archived run {request.id}", ["id"])
    change(home, lambda s: {"pinned_runs": pinned(s.pinned_runs, request.id, request.pinned)})
    tidy(context)
    return settings_show(SettingsRequest(), context)


def runs_delete(request: DeleteRunRequest, context: Context = Context()) -> RunDeleted:
    """Remove an archived run, pinned or not, and its pin."""
    home = context.store().home
    archive = Archive(home / RUNS)
    if request.id not in archive:
        raise not_found("unknown_run", f"no archived run {request.id}", ["id"])
    size = archive.size(request.id)
    with guarded():
        check_inside(home, [archive.root])
        archive.delete(request.id)
    change(home, lambda s: {"pinned_runs": pinned(s.pinned_runs, request.id, False)})
    tidy(context)
    return RunDeleted(id=request.id, bytes=size)


def storage_usage(request: UsageRequest, context: Context = Context()) -> StorageUsage:
    """How much the current data, other versions, panels and runs take, the limits, and what cleanup would free."""
    store = context.store()
    settings, _ = load(store.home)
    found = usage(store, Archive(store.home / RUNS), _cache(context), settings)
    free = found.free_versions + found.free_cache + found.free_runs
    limit = int(settings.runs_limit_gb * (1 << 30))
    return StorageUsage(**vars(found), runs_limit=limit, keep_versions=settings.keep_versions, free=free)


def storage_cleanup(request: CleanupRequest, context: Context = Context()) -> Cleanup:
    """Remove what cleanup does not keep (only count with dry_run); usage before and after."""
    store = context.store()
    archive, settings = Archive(store.home / RUNS), load(store.home)[0]
    before = storage_usage(UsageRequest(), context)
    files, runs, panels = [], [], []
    if not request.dry_run:
        with guarded():
            files = clean_versions(store, archive, settings)
            runs = prune_runs(archive, settings)
            panels = prune_panels(_cache(context), store, settings)
    after = storage_usage(UsageRequest(), context)
    return Cleanup(before=before, after=after, files_removed=len(files), runs_removed=runs, panels_removed=len(panels))


def tidy(context: Context) -> None:
    """After a commit or a settings change: versions cleanup does not keep, and panels of other versions than the one
    in use, go; skipped while another writer holds the store lock."""
    store = context.store()
    if store.manifest() is None:
        return
    settings = load(store.home)[0]
    try:
        clean_versions(store, Archive(store.home / RUNS), settings)
    except (StoreLocked, OutsideRoot) as error:
        log.info("tidy skipped: %s", error)
    prune_panels(_cache(context), store, settings)


def _cache(context: Context) -> Path:
    return Path(context.cache) if context.cache is not None else context.store().home / "cache"


def _out(settings, problems: list[str]) -> SettingsOut:
    fields = {k: list(v) if isinstance(v, tuple) else v for k, v in vars(settings).items()}
    return SettingsOut(**fields, problems=problems)
