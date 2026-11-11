"""Saved strategies (D16): each id's versions as write-once files <n>.json, indexed by its meta.json.

The version files are the truth. An index that disagrees with them is rebuilt in memory and written back only by save
or archive, which hold the id's lock.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from wequant.canonical import digest, document
from wequant.data.files import atomic_write, utc_now
from wequant.data.store import exclusive
from wequant.strategy.errors import SpecError
from wequant.strategy.strategy import STRATEGY_VERSION, Strategy

FORMAT = 1  # bump when a version file or meta.json changes meaning
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_VERSION_FILE = re.compile(r"[1-9][0-9]*\.json")


class NotSaved(LookupError):
    """No such saved strategy (code unknown_strategy) or version (unknown_version)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Version:
    """One saved version's index entry."""

    n: int
    strategy_hash: str
    saved_at: str  # UTC ISO 8601 to the second, with offset
    strategy_version: int | None  # STRATEGY_VERSION when saved; None: unknown, rebuilt from its file
    note: str | None


@dataclass(frozen=True)
class Entry:
    """A saved strategy's index; rebuilt counts the version entries that disagreed with the files."""

    id: str
    name: str
    created_at: str
    archived: bool
    versions: tuple[Version, ...]  # by n, never empty
    rebuilt: int = 0

    @property
    def current(self) -> Version:
        return self.versions[-1]

    def version(self, n: int | None = None) -> Version:
        """Version n (None: the current one); NotSaved if there is none."""
        if n is None:
            return self.current
        for version in self.versions:
            if version.n == n:
                return version
        raise NotSaved(
            "unknown_version", f"strategy {self.id} has no version {n}; it has {[v.n for v in self.versions]}"
        )


def read_warnings(entry: Entry, version: Version) -> dict[str, tuple[str, dict]]:
    """code -> (text, params) of what reading this version cannot vouch for."""
    found = {}
    if version.strategy_version not in (None, STRATEGY_VERSION):
        params = {"saved": version.strategy_version, "current": STRATEGY_VERSION}
        text = f"saved under STRATEGY_VERSION {params['saved']}, read under {params['current']}: its meaning may differ"
        found["strategy_version_changed"] = (text, params)
    if entry.rebuilt:
        text = f"meta.json disagreed with the version files on {entry.rebuilt} versions; the files were read instead"
        found["meta_rebuilt"] = (text, {"versions": entry.rebuilt})
    return found


def strategy_hash(strategy: Strategy) -> str:
    """digest(strategy.form): what saved versions and runs are matched by."""
    return digest(strategy.form)


def check_id(id: str) -> str:
    """id, or bad_ref unless it is a strategy id."""
    if not ID.fullmatch(id):
        raise SpecError(
            "bad_ref", ("id",), f"{id!r} is not a strategy id: a-z, 0-9 and -, 1 to 40, not starting with -"
        )
    return id


class Library:
    """The saved strategies under root ($WEQUANT_HOME/strategies)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def entries(self) -> list[Entry]:
        """Every saved strategy, archived ones included, by id."""
        paths = sorted(self.root.iterdir()) if self.root.is_dir() else []
        return [entry for p in paths if ID.fullmatch(p.name) and (entry := self._read(p.name)) is not None]

    def entry(self, id: str) -> Entry:
        """The saved strategy id; NotSaved if there is none."""
        entry = self._read(check_id(id))
        if entry is None:
            raise NotSaved("unknown_strategy", f"no saved strategy {id!r}")
        return entry

    def load(self, id: str, n: int | None = None) -> tuple[Entry, Version, Strategy]:
        """Version n of id (None: the current one) with its strategy; hash_mismatch if its file was changed."""
        entry = self.entry(id)
        version = entry.version(n)
        form = json.loads(self._file(id, version.n).read_text("utf-8"))
        if digest(form) != version.strategy_hash:
            message = f"{id}@{version.n} was changed after it was saved: its hash is no longer {version.strategy_hash}"
            raise SpecError("hash_mismatch", (), message)
        return entry, version, Strategy.from_form(form)

    def saved_as(self, strategy_hash: str) -> list[tuple[str, int]]:
        """Every (id, n) saved with this content, archived strategies included."""
        return [(e.id, v.n) for e in self.entries() for v in e.versions if v.strategy_hash == strategy_hash]

    def save(
        self, strategy: Strategy, id: str | None = None, name: str | None = None, note: str | None = None
    ) -> tuple[Entry, bool]:
        """strategy as the next version of id (None: a new id); no new version (False) when it equals the current one.

        name None keeps the name (the id for a new strategy); note is kept with a new version only.
        """
        id = check_id(id) if id is not None else self._new_id()
        (self.root / id).mkdir(parents=True, exist_ok=True)
        with exclusive(self.root / id / ".lock"):
            entry = self._read(id) or Entry(id, id, _now(), False, ())
            created = not entry.versions or entry.current.strategy_hash != strategy_hash(strategy)
            if created:
                n = entry.current.n + 1 if entry.versions else 1
                path = self._file(id, n)
                if path.exists():
                    raise FileExistsError(f"{path} exists: something wrote it without holding {id}/.lock")
                atomic_write(path, strategy.key.encode() + b"\n")
                version = Version(n, strategy_hash(strategy), _now(), STRATEGY_VERSION, note)
                entry = replace(entry, versions=(*entry.versions, version))
            entry = replace(entry, name=name or entry.name, rebuilt=0)
            self._write(entry)
            return entry, created

    def archive(self, id: str, restore: bool = False) -> Entry:
        """id with its archived flag set (or cleared when restore); the files are kept."""
        self.entry(id)  # NotSaved before a lock file is made for nothing
        with exclusive(self.root / id / ".lock"):
            entry = replace(self.entry(id), archived=not restore, rebuilt=0)
            self._write(entry)
            return entry

    def _read(self, id: str) -> Entry | None:
        """id's index, rebuilt where it disagrees with the version files; None without any version file."""
        directory = self.root / id
        files = {int(p.stem): p for p in directory.glob("*.json") if _VERSION_FILE.fullmatch(p.name)}
        if not files:
            return None
        meta = _meta(directory / "meta.json")
        indexed = {v["n"]: Version(**v) for v in meta["versions"]} if meta else {}
        versions = tuple(indexed[n] if n in indexed else _rebuilt(n, files[n]) for n in sorted(files))
        rebuilt = len(files.keys() ^ indexed.keys())
        if meta is None:
            return Entry(id, id, versions[0].saved_at, False, versions, rebuilt)
        return Entry(id, meta["name"], meta["created_at"], meta["archived"], versions, rebuilt)

    def _write(self, entry: Entry) -> None:
        versions = [vars(v) for v in entry.versions]
        meta = {"id": entry.id, "name": entry.name, "created_at": entry.created_at, "archived": entry.archived}
        atomic_write(self.root / entry.id / "meta.json", document({**meta, "format": FORMAT, "versions": versions}))

    def _file(self, id: str, n: int) -> Path:
        return self.root / id / f"{n}.json"

    def _new_id(self) -> str:
        while (self.root / (id := f"s-{secrets.token_hex(4)}")).exists():
            pass
        return id


def _meta(path: Path) -> dict | None:
    """The index at path; None if it was never written."""
    if not path.exists():
        return None
    meta = json.loads(path.read_text("utf-8"))
    if meta.get("format") != FORMAT:
        raise ValueError(f"{path} has format {meta.get('format')}; this code reads format {FORMAT}")
    return meta


def _rebuilt(n: int, path: Path) -> Version:
    """The index entry of a version file meta.json does not list: saved_at its mtime, version and note unknown."""
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).isoformat(timespec="seconds")
    return Version(n, digest(json.loads(path.read_text("utf-8"))), mtime, None, None)


def _now() -> str:
    return utc_now()
