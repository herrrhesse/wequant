"""Copy-on-write Parquet store with versioned manifests.

    manifest.json                   current version; replaced atomically after all data files
    manifests/manifest.v<N>.json    every committed version
    <table>/<part>.v<N>.parquet     part is the year for partitioned tables, else the table name
    .lock                           held by the single writer; names who holds it

Data files are never modified. Commits and deleting cleanups hold the lock; reads take none.
"""

from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import re
import socket
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from wequant.data.files import atomic_write, check_inside, parquet_bytes, read_text, utc_now
from wequant.data.schema import TABLES, Table
from wequant.paths import default_home
from wequant.user_config import configured_home

KEEP_VERSIONS = 5  # the user's default (round 2): the newest five versions, plus those the data root protects
REASONS = (  # why a version was made; the service layer gives each a Chinese sentence
    "seed_import",
    "baostock_merge",
    "limit_check",
    "daily_update",
    "first_build",  # round 3: the first version, built from BaoStock on an empty data root
    "history_backfill",  # round 3: earlier years added in the background
    "own_import",  # round 3: the user's own files
    "scope_extend",  # S2.11: a wider scope's stocks over one segment of the data's days
    "members_joined",  # S2.11: new members' history, after a daily update of a data root built by index
    "names",
    "version_import",
    "demo",
    "unspecified",
    "legacy",  # read only: a version written before names and reasons
)
OWN_KEYS = ("version", "name", "reason", "created_at", "tables")  # manifest keys a commit sets, never carries
_VERSIONED = re.compile(r"\.v([0-9]+)\.parquet\Z")

if os.name == "nt":
    import msvcrt

    _LOCK_BYTE = 1 << 20  # Windows locks a byte range; lock one past the holder text so it stays readable

    def _try_lock(fd: int) -> None:
        os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class StoreLocked(RuntimeError):
    """Another writer holds a lock file of the data root: the store's or a strategy's."""

    def __init__(self, path: Path, holder: dict | None):
        super().__init__(f"{path} is held by {holder or 'an unknown writer'}")
        self.holder = holder


@dataclass
class CommitResult:
    version: int
    changed: bool
    files_written: list[str] = field(default_factory=list)
    files_reused: list[str] = field(default_factory=list)  # touched, but identical to the old file
    files_kept: list[str] = field(default_factory=list)  # not touched by this commit
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)  # touched parts only


def wequant_home() -> Path:
    """$WEQUANT_HOME, else the data root the user chose last (wequant.user_config), else the system's default; a
    command's --home comes before all of them."""
    found = os.environ.get("WEQUANT_HOME")
    return Path(found).expanduser() if found else configured_home() or default_home()


def prepare(table: Table, df: pl.DataFrame) -> pl.DataFrame:
    """Validate, drop exact duplicates and sort by key; rows that share a key but differ raise ValueError."""
    table.validate(df)
    df = df.unique(maintain_order=True)
    duplicated = df.select(pl.struct(table.key).is_duplicated()).to_series()
    if duplicated.any():
        sample = df.filter(duplicated).sort(table.key).head(6)
        raise ValueError(f"{table.name}: conflicting rows share a key {table.key}:\n{sample}")
    return df.sort(table.key)


def split_parts(table: Table, df: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """{part name: rows}; part is the year for partitioned tables, else the table name."""
    if not table.partitioned:
        return {table.name: df}
    years = df[table.date_column].dt.year()
    return {str(y): df.filter(years == y) for y in sorted(years.unique().to_list())}


def tables_digest(manifest: dict, names: Iterable[str] | None = None) -> str:
    """sha256 hex of the named tables' manifest entries (all tables by default): the identity of their content."""
    tables = manifest["tables"]
    chosen = tables if names is None else {name: tables.get(name) for name in names}
    return hashlib.sha256(json.dumps(chosen, sort_keys=True).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, home: Path | None = None, clock: Callable[[], str] | None = None):
        """The store at home (default: $WEQUANT_HOME); clock stamps each committed manifest (tests, the demo home)."""
        self.home = Path(home) if home is not None else wequant_home()
        self.clock = clock or utc_now
        self._lock_depth = 0

    # ------------------------------------------------------------ reading

    def manifest(self, version: int | None = None) -> dict | None:
        """The current manifest, or the given version; None if nothing was committed yet."""
        path = self.home / "manifest.json" if version is None else self._manifest_path(version)
        if not path.exists():
            return None
        return json.loads(read_text(path))

    def versions(self) -> list[int]:
        directory = self.home / "manifests"
        if not directory.exists():
            return []
        found = (re.fullmatch(r"manifest\.v([0-9]+)\.json", p.name) for p in directory.iterdir())
        return sorted(int(m.group(1)) for m in found if m)

    def scan(
        self,
        name: str,
        columns: list[str] | None = None,
        start: dt.date | str | None = None,
        end: dt.date | str | None = None,
        codes: Iterable[str] | None = None,
        version: int | None = None,
        manifest: dict | None = None,
    ) -> pl.LazyFrame:
        """Rows of one table at one snapshot, with columns, the date range (inclusive) and codes pushed down."""
        table = TABLES[name]
        manifest = manifest if manifest is not None else self.manifest(version)
        files = manifest["tables"].get(name, {}).get("files", []) if manifest else []
        start, end = _date(start), _date(end)
        if (start or end) and table.date_column is None:
            raise ValueError(f"{name} has no date column to filter on")
        if codes is not None and "code" not in table.schema:
            raise ValueError(f"{name} has no code column to filter on")
        if table.partitioned:
            files = [e for e in files if _year_in(int(e["part"]), start, end)]
        if files:
            frame = _scan_files([self.home / e["path"] for e in files], table)
        else:
            frame = pl.LazyFrame(schema=table.schema)
        if start is not None:
            frame = frame.filter(pl.col(table.date_column) >= start)
        if end is not None:
            frame = frame.filter(pl.col(table.date_column) <= end)
        if codes is not None:
            frame = frame.filter(pl.col("code").is_in(list(codes)))
        return frame if columns is None else frame.select(columns)

    def read_table(self, name: str, **query) -> pl.DataFrame:
        """scan(name, **query), collected."""
        return self.scan(name, **query).collect()

    # ------------------------------------------------------------ writing

    @contextmanager
    def lock(self, timeout: float = 0) -> Iterator[None]:
        """Hold the single-writer lock; StoreLocked names the holder if it is not free within timeout seconds.

        Re-entrant within one Store object.
        """
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        self.home.mkdir(parents=True, exist_ok=True)
        with exclusive(self.home / ".lock", timeout):
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0

    def commit(
        self,
        meta: dict,
        *,
        replace: dict[str, pl.DataFrame] | None = None,
        replace_parts: dict[str, pl.DataFrame] | None = None,
        upsert: dict[str, pl.DataFrame] | None = None,
        timeout: float = 0,
        reason: str = "unspecified",
        reason_params: dict | None = None,
    ) -> CommitResult:
        """Write a new version named YYYYMMDD.N (its last trade date, the next number for that date) with why it was
        made (a REASONS code and its params); every table changes through the same manifest swap.

        replace:        the table becomes exactly df.
        replace_parts:  each part df covers becomes df's rows for it; other parts are kept.
        upsert:         df's rows replace rows with the same key or are added; all other rows
                        are kept. upsert never deletes rows.

        Tables and parts not touched keep their manifest entries unread. A touched part whose
        content is unchanged keeps its file, and a commit that changes no table and no meta value
        other than code_version creates no version. meta is merged into the previous manifest's keys.
        """
        # Compare meta as the manifest will hold it (a tuple comes back a list); bad meta fails before any write.
        meta = json.loads(json.dumps(meta))
        if reason not in REASONS[:-1]:
            raise ValueError(f"reason {reason!r} is not one of {REASONS[:-1]}")
        why = {"code": reason, "params": json.loads(json.dumps(reason_params or {}))}
        writes: dict[str, tuple[str, pl.DataFrame]] = {}
        for mode, tables in (("replace", replace), ("replace_parts", replace_parts), ("upsert", upsert)):
            for name, df in (tables or {}).items():
                if name in writes:
                    raise ValueError(f"{name} appears in more than one write mode")
                writes[name] = (mode, df)

        self._guard(writes)
        with self.lock(timeout):
            current = self.manifest()
            old_tables = current["tables"] if current else {}
            result = CommitResult(version=self._next_version(current), changed=False)
            new_tables = dict(old_tables)
            for name, (mode, df) in writes.items():
                old_files = old_tables.get(name, {}).get("files", [])
                new_tables[name] = {"files": self._write_table(name, mode, df, old_files, result)}
            touched = {*result.files_written, *result.files_reused}
            result.files_kept = [e["path"] for t in new_tables.values() for e in t["files"] if e["path"] not in touched]

            if current is not None and new_tables == old_tables and _same_meta(current, meta):
                result.version = current["version"]
                return result
            carried = {k: v for k, v in (current or {}).items() if k not in OWN_KEYS}
            body, now = {**carried, **meta, "tables": new_tables}, self.clock()
            name = self._next_name(_name_day(body | {"created_at": now}))
            manifest = {"version": result.version, "name": name, "reason": why, "created_at": now, **body}
            data = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
            self._manifest_path(result.version).parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self._manifest_path(result.version), data)
            atomic_write(self.home / "manifest.json", data)
            result.changed = True
            return result

    def cleanup(
        self, keep: int = KEEP_VERSIONS, dry_run: bool = True, timeout: float = 0, protect: Iterable[int] = ()
    ) -> list[Path]:
        """Files only versions other than the newest keep and protect need (the current one is always kept), plus
        leftover temp files; deleted (under the lock) only when dry_run is False."""
        if dry_run:
            return self._unneeded(keep, set(protect))
        self._guard(TABLES)
        with self.lock(timeout):
            doomed = self._unneeded(keep, set(protect))
            for path in doomed:
                path.unlink()
            return doomed

    def add(self, manifest: dict, files: dict[str, bytes], reason: str, reason_params: dict | None = None) -> int:
        """Add a version made elsewhere, as the next version number but not the current one: each of its files under
        this store's name for it (a kept file with the same content is reused), its manifest's own keys new. files
        maps the manifest's paths to their bytes. Returns the new version."""
        self._guard(manifest["tables"])
        with self.lock():
            version = max([0, *self.versions()]) + 1
            known = {e["sha256"]: e["path"] for v in self.versions() for e in version_files(self.manifest(v))}
            tables = {
                name: {
                    "files": [
                        self._added(name, entry, files[entry["path"]], version, known) for entry in table["files"]
                    ]
                }
                for name, table in manifest["tables"].items()
            }
            body = {k: v for k, v in manifest.items() if k not in OWN_KEYS} | {"tables": tables}
            why = {"code": reason, "params": json.loads(json.dumps(reason_params or {}))}
            added = {"version": version, "name": self._next_name(_name_day(manifest)), "reason": why}
            data = (
                json.dumps(added | {"created_at": self.clock(), **body}, indent=1, ensure_ascii=False).encode() + b"\n"
            )
            atomic_write(self._manifest_path(version), data)
            return version

    # ------------------------------------------------------------ internals

    def _next_name(self, day: str) -> str:
        """day.N with N one past the highest number a kept version of that day has, so it is new among them."""
        names = (version_name(found) for v in self.versions() if (found := self.manifest(v)) is not None)
        taken = [int(n.split(".")[1]) for n in names if n.split(".")[0] == day]
        return f"{day}.{max(taken, default=0) + 1}"

    def _write_table(
        self, name: str, mode: str, df: pl.DataFrame, old_files: list[dict], result: CommitResult
    ) -> list[dict]:
        table = TABLES[name]
        old = {e["part"]: e for e in old_files}
        parts = split_parts(table, prepare(table, df))
        if mode == "upsert":
            parts = {part: self._merge(table, old.get(part), rows) for part, rows in parts.items()}
        entries = {} if mode == "replace" else dict(old)
        result.hashes[name] = {}
        for part, rows in parts.items():
            entries[part] = self._write_part(name, part, rows, old.get(part), result)
        return [entries[part] for part in sorted(entries)]

    def _merge(self, table: Table, old: dict | None, rows: pl.DataFrame) -> pl.DataFrame:
        if old is None:
            return rows
        stored = pl.scan_parquet(str(self.home / old["path"])).collect()
        kept = stored.join(rows.select(table.key), on=list(table.key), how="anti")
        return prepare(table, pl.concat([kept, rows]))

    def _write_part(self, name: str, part: str, rows: pl.DataFrame, old: dict | None, result: CommitResult) -> dict:
        data = parquet_bytes(rows)
        digest = hashlib.sha256(data).hexdigest()
        result.hashes[name][part] = digest
        if old is not None and old["sha256"] == digest and (self.home / old["path"]).exists():
            result.files_reused.append(old["path"])
            return old
        rel = f"{name}/{part}.v{result.version}.parquet"
        (self.home / name).mkdir(parents=True, exist_ok=True)
        atomic_write(self.home / rel, data)
        result.files_written.append(rel)
        return {"part": part, "path": rel, "rows": rows.height, "sha256": digest}

    def _guard(self, tables: Iterable[str]) -> None:
        """OutsideRoot unless the manifests and these tables' folders resolve inside the data root: a data root whose
        folders link to another store can be read, never written or cleaned."""
        check_inside(
            self.home, [self.home / "manifest.json", self.home / "manifests", *(self.home / t for t in tables)]
        )

    def _next_version(self, current: dict | None) -> int:
        """One past the current version, past any added version there: a manifest an interrupted commit left is
        written over, an added one never."""
        version = (current["version"] if current else 0) + 1
        while (found := self.manifest(version)) is not None and version_reason(found)["code"] == "version_import":
            version += 1
        return version

    def _added(self, name: str, entry: dict, data: bytes, version: int, known: dict[str, str]) -> dict:
        """The manifest entry of one added file: a kept file with its sha256, else the file written as this version's."""
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"{entry['path']}: its bytes do not have the sha256 its manifest records")
        if digest in known and (self.home / known[digest]).exists():
            return entry | {"path": known[digest]}
        rel = f"{name}/{entry['part']}.v{version}.parquet"
        atomic_write(self.home / rel, data)
        return entry | {"path": rel}

    def _unneeded(self, keep: int, protect: set[int] = frozenset()) -> list[Path]:
        versions = self.versions()
        current = self.manifest()
        kept = (versions[-keep:] if keep > 0 else []) + [v for v in versions if v in protect]
        kept += [current["version"]] if current else []
        referenced = set()
        for v in kept:
            for entry in self.manifest(v)["tables"].values():
                referenced.update(e["path"] for e in entry["files"])
        doomed = [self._manifest_path(v) for v in versions if v not in kept]
        for directory in [self.home, self.home / "manifests", *(self.home / name for name in TABLES)]:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                rel = path.relative_to(self.home).as_posix()
                stale_tmp = path.name.startswith(".") and path.name.endswith(".tmp")
                unreferenced = directory != self.home and _VERSIONED.search(path.name) and rel not in referenced
                if stale_tmp or unreferenced:
                    doomed.append(path)
        return doomed

    def _manifest_path(self, version: int) -> Path:
        return self.home / "manifests" / f"manifest.v{version}.json"


@contextmanager
def exclusive(path: Path, timeout: float = 0, command: str | None = None) -> Iterator[None]:
    """Hold the lock file at path, which names the holder (and its command, when given); StoreLocked if it is not free
    within timeout seconds. The same on Linux (flock) and Windows (a locked byte past the holder text)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _acquire(fd, path, timeout)
    except BaseException:
        os.close(fd)
        raise
    try:
        holder = {"pid": os.getpid(), "host": socket.gethostname(), "started": utc_now()}
        _write_fd(fd, json.dumps(holder | ({"command": command} if command else {})))
        yield
    finally:
        _write_fd(fd, "")
        _unlock(fd)
        os.close(fd)


def _acquire(fd: int, path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            _try_lock(fd)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise StoreLocked(path, _holder(path)) from None
            time.sleep(0.05)


def _holder(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _write_fd(fd: int, text: str) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, text.encode("utf-8"))
    os.fsync(fd)


def _scan_files(paths: list[Path], table: Table) -> pl.LazyFrame:
    """The files' rows in the table's columns: a file written before a column was added reads it as null, and one
    that has a column the table dropped loses it."""
    if all(_file_schema(path) == table.schema for path in paths):
        return pl.scan_parquet([str(path) for path in paths])
    return pl.concat([_conformed(path, table) for path in paths])


def _conformed(path: Path, table: Table) -> pl.LazyFrame:
    present = _file_schema(path)
    return pl.scan_parquet(path).select(
        pl.col(c).cast(t) if c in present else pl.lit(None, dtype=t).alias(c) for c, t in table.schema.items()
    )


@functools.lru_cache(maxsize=8192)
def _file_schema(path: Path) -> dict:
    """A data file's columns; data files never change, so the answer is kept."""
    return dict(pl.read_parquet_schema(path))


def _date(value: dt.date | str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if isinstance(value, str) else value


def _year_in(year: int, start: dt.date | None, end: dt.date | None) -> bool:
    return (start is None or year >= start.year) and (end is None or year <= end.year)


def version_name(manifest: dict) -> str:
    """The version's display name: the one it was written with, else (a legacy version) its day and its own number,
    which no cleanup of other versions can change."""
    return manifest.get("name") or f"{_name_day(manifest)}.{manifest['version']}"


def version_reason(manifest: dict) -> dict:
    """{code, params} of why the version was made; legacy for one written before reasons."""
    return manifest.get("reason") or {"code": "legacy", "params": {}}


def version_files(manifest: dict) -> list[dict]:
    """The file entries of every table of a manifest: part, path, rows, sha256."""
    return [entry for table in manifest["tables"].values() for entry in table["files"]]


def _name_day(manifest: dict) -> str:
    """YYYYMMDD of the version's last trade date (of its creation when it has none)."""
    return str(manifest.get("last_trade_date") or manifest["created_at"][:10]).replace("-", "")


def _same_meta(current: dict, meta: dict) -> bool:
    """True when meta changes no value but code versions, which alone never make a version."""
    return _versionless({k: current.get(k) for k in meta}) == _versionless(meta)


def _versionless(value):
    if isinstance(value, dict):
        return {k: _versionless(v) for k, v in value.items() if k != "code_version"}
    return value
