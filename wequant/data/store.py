"""Copy-on-write Parquet store with versioned manifests.

    manifest.json                   current version; replaced atomically after all data files
    manifests/manifest.v<N>.json    every committed version
    <table>/<part>.v<N>.parquet     part is the year for partitioned tables, else the table name

Data files are never modified. A file identical to the one the current version already
uses for the same part is reused, and a commit that changes nothing creates no version.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from wequant.data.schema import TABLES, Table

KEEP_VERSIONS = 3
_VERSIONED = re.compile(r"\.v([0-9]+)\.parquet\Z")


def wequant_home() -> Path:
    return Path(os.environ.get("WEQUANT_HOME", "~/.wequant")).expanduser()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def parquet_bytes(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


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
    """{part name: rows}; part is the year for year-partitioned tables, else the table name."""
    if table.year_partition is None:
        return {table.name: df}
    years = df[table.year_partition].dt.year()
    return {str(y): df.filter(years == y) for y in sorted(years.unique().to_list())}


@dataclass
class CommitResult:
    version: int
    changed: bool
    files_written: list[str] = field(default_factory=list)
    files_reused: list[str] = field(default_factory=list)
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)


class Store:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home is not None else wequant_home()

    # ------------------------------------------------------------ reading

    def manifest(self, version: int | None = None) -> dict | None:
        """The current manifest, or the given version; None if nothing was committed yet."""
        path = self.home / "manifest.json" if version is None else self._manifest_path(version)
        if not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))

    def read_table(self, name: str, version: int | None = None, manifest: dict | None = None) -> pl.DataFrame:
        """All rows of a table at one version (default: current)."""
        manifest = manifest if manifest is not None else self.manifest(version)
        if manifest is None or name not in manifest["tables"]:
            raise KeyError(f"table {name} is not in the store")
        files = manifest["tables"][name]["files"]
        frames = [pl.read_parquet(self.home / entry["path"]) for entry in files]
        if not frames:
            return pl.DataFrame(schema=TABLES[name].schema)
        return pl.concat(frames, how="vertical")

    def versions(self) -> list[int]:
        directory = self.home / "manifests"
        if not directory.exists():
            return []
        found = (re.fullmatch(r"manifest\.v([0-9]+)\.json", p.name) for p in directory.iterdir())
        return sorted(int(m.group(1)) for m in found if m)

    # ------------------------------------------------------------ writing

    def commit(self, tables: dict[str, pl.DataFrame], meta: dict) -> CommitResult:
        """Write the given tables as a new version; other tables carry over. meta goes into the manifest."""
        current = self.manifest()
        version = (current["version"] if current else 0) + 1
        current_tables = current["tables"] if current else {}
        new_tables = {name: entry for name, entry in current_tables.items() if name not in tables}
        result = CommitResult(version=version, changed=False)

        for name, df in tables.items():
            table = TABLES[name]
            old_files = {e["part"]: e for e in current_tables.get(name, {}).get("files", [])}
            entries = []
            result.hashes[name] = {}
            for part, rows in split_parts(table, prepare(table, df)).items():
                data = parquet_bytes(rows)
                digest = hashlib.sha256(data).hexdigest()
                result.hashes[name][part] = digest
                old = old_files.get(part)
                if old is not None and old["sha256"] == digest and (self.home / old["path"]).exists():
                    entries.append(old)
                    result.files_reused.append(old["path"])
                    continue
                rel = f"{name}/{part}.v{version}.parquet"
                path = self.home / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, data)
                entries.append({"part": part, "path": rel, "rows": rows.height, "sha256": digest})
                result.files_written.append(rel)
            new_tables[name] = {"files": entries}

        same_meta = current is not None and all(current.get(k) == v for k, v in meta.items())
        if current is not None and new_tables == current_tables and same_meta:
            result.version = current["version"]
            return result

        manifest = {
            "version": version,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **meta,
            "tables": new_tables,
        }
        data = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
        self._manifest_path(version).parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._manifest_path(version), data)
        _atomic_write(self.home / "manifest.json", data)
        result.changed = True
        return result

    def cleanup(self, keep: int = KEEP_VERSIONS, dry_run: bool = True) -> list[Path]:
        """Files only older versions need, plus leftover temp files; deleted only when dry_run is False."""
        versions = self.versions()
        kept = versions[-keep:] if keep > 0 else []
        referenced = set()
        for v in kept:
            for entry in self.manifest(v)["tables"].values():
                referenced.update(e["path"] for e in entry["files"])
        doomed = [self._manifest_path(v) for v in versions if v not in kept]
        directories = [self.home, self.home / "manifests", *(self.home / name for name in TABLES)]
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                rel = path.relative_to(self.home).as_posix()
                stale_tmp = path.name.startswith(".") and path.name.endswith(".tmp")
                unreferenced = directory != self.home and _VERSIONED.search(path.name) and rel not in referenced
                if stale_tmp or unreferenced:
                    doomed.append(path)
        if not dry_run:
            for path in doomed:
                path.unlink()
        return doomed

    def _manifest_path(self, version: int) -> Path:
        return self.home / "manifests" / f"manifest.v{version}.json"
