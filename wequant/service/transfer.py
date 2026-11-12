"""Exports and imports (round 2): a data version (.wqver), a run with its strategy (.wqrun) and a strategy (.wqstrategy)
as one file each. An export goes to the given path, else <home>/exports/; an import adds what the file holds and never
overwrites: a version comes in as a new version (not the current one), a run under its own id, a strategy under a new
name or id when its own are taken. The same content always gives the same file, and an imported run's files are
byte for byte the exported ones."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from wequant.data.files import atomic_write
from wequant.data.store import version_name
from wequant.runs.archive import RUNS, Archive, RunConflict
from wequant.service.base import Context, DataRequest, ErrorDetail, Model, manifest, not_found, read_version
from wequant.service.details import usage
from wequant.service.specs import Name
from wequant.service.storage import tidy
from wequant.storage.files import (
    RUN,
    STRATEGY,
    VERSION,
    export_run,
    export_strategy,
    export_version,
    import_run,
    import_strategy,
    import_version,
)
from wequant.strategy import SpecError
from wequant.strategy.library import NotSaved

EXPORTS, IMPORTS = "exports", "imports"  # under the data root: files exports wrote, files uploaded to import


class ExportVersionRequest(DataRequest):
    """A data version as one file: its manifest and every file it reads."""

    path: str | None = None
    """Where to write it; null: <home>/exports/v<N>_<name>.wqver."""


class ExportRunRequest(Model):
    """An archived run as one file, with its strategy."""

    id: Name
    path: str | None = None
    """Where to write it; null: <home>/exports/<id>.wqrun."""


class ExportStrategyRequest(Model):
    """A saved strategy as one file: its name and current version, or every version."""

    id: str
    history: bool = False
    """Every saved version, not only the current one."""
    path: str | None = None
    """Where to write it; null: <home>/exports/<id>.wqstrategy."""


class Exported(Model):
    """The file written."""

    path: str
    bytes: int


class ImportRequest(Model):
    """A file to import, as an export wrote it."""

    path: str


class VersionImported(Model):
    """The version the file became."""

    data_version: int
    data_version_name: str
    """Its display name here."""
    source_name: str
    """Its display name where it was exported."""


class RunImported(Model):
    """The run the file held."""

    id: str
    added: bool
    """False: it was archived here already, with the same content."""


class StrategyImported(Model):
    """The strategy the file became."""

    id: str
    name: str
    versions: int


def data_export(request: ExportVersionRequest, context: Context = Context()) -> Exported:
    """Write a data version (default: the one in use) to one file."""
    store = context.store()
    found = manifest(store, read_version(context, request.data_version))
    path = _target(context, request.path, f"v{found['version']}_{version_name(found)}{VERSION}")
    return Exported(path=str(path), bytes=export_version(store, found["version"], path))


def data_import(request: ImportRequest, context: Context = Context()) -> VersionImported:
    """Add the version in a file as a new version (reason version_import); use it with data_use_version."""
    store = context.store()
    with _reading(request.path) as path:
        version = import_version(store, path)
    tidy(context)
    added = store.manifest(version)
    source = added["reason"]["params"]["name"]
    return VersionImported(data_version=version, data_version_name=version_name(added), source_name=source)


def runs_export(request: ExportRunRequest, context: Context = Context()) -> Exported:
    """Write an archived run and its strategy to one file."""
    archive = Archive(context.store().home / RUNS)
    if request.id not in archive:
        raise not_found("unknown_run", f"no archived run {request.id}", ["id"])
    path = _target(context, request.path, f"{request.id}{RUN}")
    return Exported(path=str(path), bytes=export_run(archive, request.id, path))


def runs_import(request: ImportRequest, context: Context = Context()) -> RunImported:
    """Archive the run in a file; rerunning it needs its store version here."""
    with _reading(request.path) as path:
        run_id, added = import_run(Archive(context.store().home / RUNS), path)
    return RunImported(id=run_id, added=added)


def strategy_export(request: ExportStrategyRequest, context: Context = Context()) -> Exported:
    """Write a saved strategy to one file."""
    library = context.library()
    try:
        library.entry(request.id)
    except (NotSaved, SpecError) as error:
        raise not_found("unknown_strategy", str(error), ["id"]) from None
    path = _target(context, request.path, f"{request.id}{STRATEGY}")
    return Exported(path=str(path), bytes=export_strategy(library, request.id, path, request.history))


def strategy_import(request: ImportRequest, context: Context = Context()) -> StrategyImported:
    """Save the strategy in a file, under a new name or id when its own are taken."""
    with _reading(request.path) as path:
        entry = import_strategy(context.library(), path)
    return StrategyImported(id=entry.id, name=entry.name, versions=len(entry.versions))


def exported(context: Context, name: str) -> Path | None:
    """The file named name an export wrote to <home>/exports/; None for another name or no such file."""
    path = context.store().home / EXPORTS / name
    return path if _exchange_name(name) and path.is_file() else None


def receive(context: Context, name: str, data: bytes) -> Path:
    """Keep an uploaded file to import as <home>/imports/<name>; USAGE bad_file unless name is an export's file name."""
    if not _exchange_name(name):
        message = f"{name!r} is not a {VERSION}, {RUN} or {STRATEGY} file name"
        raise usage([ErrorDetail(code="bad_file", message=message, loc=["name"])])
    path = context.store().home / IMPORTS / name
    atomic_write(path, data)
    return path


def _exchange_name(name: str) -> bool:
    """A plain file name with an export's suffix."""
    return Path(name).name == name and Path(name).suffix in (VERSION, RUN, STRATEGY)


def _target(context: Context, given: str | None, name: str) -> Path:
    path = Path(given) if given else context.store().home / EXPORTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _reading(given: str) -> Iterator[Path]:
    """The path of a file to import: NOT_FOUND without it, USAGE bad_file when it is not what an export writes."""
    path = Path(given)
    if not path.is_file():
        raise not_found("no_file", f"no file {path}", ["path"])
    try:
        yield path
    except (zipfile.BadZipFile, KeyError, ValueError, RunConflict, SpecError) as error:
        detail = ErrorDetail(code="bad_file", message=f"{path} cannot be imported: {error}", loc=["path"])
        raise usage([detail]) from None
