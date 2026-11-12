"""Runs, data versions and strategies as single files, to move between data roots: a run (.wqrun) and a version
(.wqver) as zip files whose bytes depend only on their content, a strategy (.wqstrategy) as canonical JSON."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from wequant.canonical import canonical
from wequant.data.files import atomic_write
from wequant.data.store import Store, version_files, version_name
from wequant.runs.archive import Archive
from wequant.strategy import Strategy
from wequant.strategy.library import Entry, Library

RUN, VERSION, STRATEGY = ".wqrun", ".wqver", ".wqstrategy"
FORMAT = 1
EPOCH = (1980, 1, 1, 0, 0, 0)  # every entry's time, so the same content gives the same bytes


def export_run(archive: Archive, run_id: str, path: Path) -> int:
    """Write the run's archive files and its strategy (strategy.json) to path; its size."""
    files = archive.files(run_id)
    strategy = json.loads(files["run.json"])["inputs"]["description"]["strategy"]
    return _zip(path, files | {"strategy.json": canonical(strategy).encode() + b"\n"})


def import_run(archive: Archive, path: Path) -> tuple[str, bool]:
    """(the run's id, whether it was added) of the run in path; one archived with the same content is kept."""
    files = _unzip(path)
    files.pop("strategy.json", None)
    run_id = json.loads(files["run.json"])["id"]
    return run_id, archive.add(run_id, files)


def export_version(store: Store, version: int, path: Path) -> int:
    """Write the version's manifest and every file it reads to path; its size."""
    manifest = store.manifest(version)
    files = {entry["path"]: (store.home / entry["path"]).read_bytes() for entry in version_files(manifest)}
    return _zip(path, files | {"manifest.json": json.dumps(manifest, indent=1, ensure_ascii=False).encode()})


def import_version(store: Store, path: Path) -> int:
    """Add the version in path to the store as a new version, not the current one (reason version_import, its name
    then kept as a param); the new version."""
    files = _unzip(path)
    manifest = json.loads(files.pop("manifest.json"))
    return store.add(manifest, files, "version_import", {"name": version_name(manifest)})


def export_strategy(library: Library, id: str, path: Path, history: bool = False) -> int:
    """Write the strategy's name and current version (every version with history) to path; its size."""
    entry = library.entry(id)
    versions = entry.versions if history else (entry.current,)
    saved = [library.load(id, v.n)[2].form | {} for v in versions]
    doc = {
        "format": FORMAT,
        "id": entry.id,
        "name": entry.name,
        "versions": [{"note": v.note, "strategy": form} for v, form in zip(versions, saved, strict=True)],
    }
    data = canonical(doc).encode() + b"\n"
    atomic_write(Path(path), data)
    return len(data)


def import_strategy(library: Library, path: Path) -> Entry:
    """Save the strategy in path, its versions in order; a name or id already taken gets a new one, nothing is
    overwritten."""
    doc = json.loads(Path(path).read_text("utf-8"))
    entries = library.entries()
    name = _free(doc["name"], {e.name for e in entries}, " ({n})")
    id = _free(doc["id"], {e.id for e in entries}, "-{n}")
    for version in doc["versions"]:
        entry, _ = library.save(Strategy.from_form(version["strategy"]), id, name, version["note"])
        id = entry.id
    return entry


def _free(wanted: str, taken: set[str], suffix: str) -> str:
    """wanted, or wanted with suffix numbered from 2 while taken."""
    found, n = wanted, 1
    while found in taken:
        n += 1
        found = wanted + suffix.format(n=n)
    return found


def _zip(path: Path, files: dict[str, bytes]) -> int:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            archive.writestr(zipfile.ZipInfo(name, date_time=EPOCH), files[name])
    atomic_write(Path(path), buffer.getvalue())
    return len(buffer.getvalue())


def _unzip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(Path(path)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}
