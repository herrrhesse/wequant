"""Download, verify and extract a chenditc/investment_data release."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

REPO = "chenditc/investment_data"
ARCHIVE = "qlib_bin.tar.gz"
MANIFEST = "qlib_bin.manifest.json"
EXTRACTED_HASHES = "extracted.sha256"
_ROOT = "qlib_bin"
_CHUNK = 1 << 20

# Manifest keys, in order, and the members every archive must have (as in upstream qlib/validate_archive.py).
MANIFEST_KEYS = (
    "release_tag",
    "target_trade_date",
    "future_start_date",
    "future_end_date",
    "dolt_commit",
    "investment_data_commit",
    "qlib_commit",
    "image_digest",
    "archive_size_bytes",
    "archive_sha256",
)
REQUIRED_MEMBERS = (
    "calendars/day.txt",
    "calendars/day_future.txt",
    "instruments/all.txt",
    "instruments/csi300.txt",
    "instruments/csi500.txt",
    "instruments/csi800.txt",
    "instruments/csi1000.txt",
    "instruments/csiall.txt",
)


class VerificationError(RuntimeError):
    """The release does not match its manifest."""


def resolve_tag(tag: str) -> str:
    """The concrete release tag; "latest" is resolved through GitHub's redirect."""
    if tag != "latest":
        return tag
    with _open(f"https://github.com/{REPO}/releases/latest", method="HEAD") as response:
        match = re.search(r"/releases/tag/([^/?#]+)$", response.geturl())
    if match is None:
        raise RuntimeError("cannot resolve the latest release tag")
    return match.group(1)


def download(tag: str, dest: Path) -> dict[str, str]:
    """Fetch the archive and manifest into dest unless already there; files are saved unchanged."""
    dest.mkdir(parents=True, exist_ok=True)
    status = {}
    for name in (MANIFEST, ARCHIVE):
        target = dest / name
        if target.exists():
            status[name] = "already present"
            continue
        part = target.with_name(name + ".part")
        with _open(f"https://github.com/{REPO}/releases/download/{tag}/{name}") as response, open(part, "wb") as out:
            shutil.copyfileobj(response, out, _CHUNK)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, target)
        status[name] = "downloaded"
    return status


def verify(tag: str, raw_dir: Path) -> dict:
    """Check manifest format, archive size and sha256, member paths, required members and calendar dates."""
    manifest = _load_manifest(raw_dir / MANIFEST, tag)
    archive = raw_dir / ARCHIVE
    size, digest = archive.stat().st_size, "sha256:" + sha256_file(archive)
    if size != manifest["archive_size_bytes"]:
        raise VerificationError(f"archive size {size} != manifest {manifest['archive_size_bytes']}")
    if digest != manifest["archive_sha256"]:
        raise VerificationError(f"archive {digest} != manifest {manifest['archive_sha256']}")

    required, n_files, seen = {}, 0, set()
    with tarfile.open(archive, mode="r:gz") as tar:
        for member in tar:
            rel = _member_path(member)
            if rel is None or rel in seen:
                raise VerificationError(f"unsafe or duplicate archive member {member.name!r}")
            seen.add(rel)
            if member.isfile():
                n_files += 1
                if rel in REQUIRED_MEMBERS:
                    required[rel] = tar.extractfile(member).read()
    missing = [m for m in REQUIRED_MEMBERS if m not in required]
    if missing:
        raise VerificationError(f"archive is missing {missing}")

    day = _calendar_lines(required["calendars/day.txt"])
    future = _calendar_lines(required["calendars/day_future.txt"])
    target = manifest["target_trade_date"]
    if day[-1] != target:
        raise VerificationError(f"day.txt ends {day[-1]}, target_trade_date is {target}")
    for name in REQUIRED_MEMBERS[2:]:
        last = max(end for _, _, end in _instrument_lines(required[name], name))
        if last != target:
            raise VerificationError(f"{name} ends {last}, target_trade_date is {target}")
    if future[: len(day)] != day or len(future) <= len(day):
        raise VerificationError("day_future.txt does not extend day.txt")
    if (future[len(day)], future[-1]) != (manifest["future_start_date"], manifest["future_end_date"]):
        raise VerificationError("day_future.txt does not match the manifest's future dates")

    return {
        "manifest": manifest,
        "manifest_sha256": "sha256:" + sha256_file(raw_dir / MANIFEST),
        "archive_size_bytes": size,
        "archive_sha256": digest,
        "archive_files": n_files,
        "calendar_days": len(day),
        "calendar_first": day[0],
        "calendar_last": day[-1],
        "future_days": len(future) - len(day),
    }


def extract(raw_dir: Path) -> dict:
    """Extract the verified archive to raw_dir/extracted/, without its qlib_bin/ prefix.

    The manifest hashes only the archive, so every member is hashed while read and again on
    disk. The hashes are kept in extracted.sha256; later runs re-check against them.
    """
    target, hash_file = raw_dir / "extracted", raw_dir / EXTRACTED_HASHES
    if target.exists() and hash_file.exists():
        pairs = (line.split("  ", 1) for line in hash_file.read_text("utf-8").splitlines())
        hashes = {rel: digest for digest, rel in pairs}
        _check_extracted(target, hashes)
        return {"status": "already extracted, re-verified", "files": len(hashes)}

    staging = raw_dir / "extracted.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    hashes = {}
    with tarfile.open(raw_dir / ARCHIVE, mode="r:gz") as tar:
        for member in tar:
            rel = _member_path(member)
            if rel is None:
                raise VerificationError(f"unsafe archive member {member.name!r}")
            path = staging / rel
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with tar.extractfile(member) as src, open(path, "wb") as out:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    digest.update(chunk)
                    out.write(chunk)
            hashes[rel] = digest.hexdigest()
    _check_extracted(staging, hashes)
    tmp_hash_file = hash_file.with_name(EXTRACTED_HASHES + ".tmp")
    tmp_hash_file.write_text("".join(f"{d}  {rel}\n" for rel, d in sorted(hashes.items())), "utf-8")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    os.replace(tmp_hash_file, hash_file)
    return {"status": "extracted and verified", "files": len(hashes)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open(url: str, method: str = "GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "wequant"})
    return urllib.request.urlopen(request, timeout=60)


def _is_date(value) -> bool:
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _load_manifest(path: Path, tag: str) -> dict:
    pairs = json.loads(path.read_text("utf-8"), object_pairs_hook=lambda items: items)
    if [key for key, _ in pairs] != list(MANIFEST_KEYS):
        raise VerificationError(f"manifest keys {[key for key, _ in pairs]} differ from {list(MANIFEST_KEYS)}")
    manifest = dict(pairs)
    if not all(_is_date(manifest[key]) for key in MANIFEST_KEYS[:4]):
        raise VerificationError("manifest dates are not YYYY-MM-DD")
    if manifest["release_tag"] != tag:
        raise VerificationError(f"manifest release_tag {manifest['release_tag']} != {tag}")
    size = manifest["archive_size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise VerificationError(f"manifest archive_size_bytes {size!r} is invalid")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest["archive_sha256"])) is None:
        raise VerificationError(f"manifest archive_sha256 {manifest['archive_sha256']!r} is invalid")
    return manifest


def _member_path(member: tarfile.TarInfo) -> str | None:
    """Path below qlib_bin/ ("" for qlib_bin/ itself), or None for anything unsafe."""
    name = member.name.rstrip("/")
    if not name or "\\" in member.name or member.name.startswith("/") or posixpath.normpath(name) != name:
        return None
    if any(part in ("", ".", "..") for part in PurePosixPath(name).parts):
        return None
    if not (member.isdir() or member.isfile()) or getattr(member, "sparse", None):
        return None
    if name == _ROOT:
        return ""
    return name[len(_ROOT) + 1 :] if name.startswith(_ROOT + "/") else None


def _lines(raw: bytes, name: str) -> list[str]:
    text = raw.decode("utf-8")
    lines = text[:-1].split("\n")
    if not text.endswith("\n") or "\r" in text or not all(lines):
        raise VerificationError(f"{name} is not clean newline-terminated text")
    return lines


def _calendar_lines(raw: bytes) -> list[str]:
    lines = _lines(raw, "calendar")
    if not all(_is_date(line) for line in lines) or lines != sorted(set(lines)):
        raise VerificationError("calendar dates are malformed, unsorted or duplicated")
    return lines


def _instrument_lines(raw: bytes, name: str) -> list[tuple[str, str, str]]:
    rows = [tuple(line.split("\t")) for line in _lines(raw, name)]
    for row in rows:
        if len(row) != 3 or not row[0] or not _is_date(row[1]) or not _is_date(row[2]) or row[1] > row[2]:
            raise VerificationError(f"{name}: malformed line {row}")
    if rows != sorted(set(rows)):
        raise VerificationError(f"{name}: rows are unsorted or duplicated")
    return rows


def _check_extracted(root: Path, hashes: dict[str, str]) -> None:
    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if on_disk != set(hashes):
        raise VerificationError(f"extracted files differ from the archive: {sorted(on_disk ^ set(hashes))[:5]}")
    for rel, digest in hashes.items():
        if sha256_file(root / rel) != digest:
            raise VerificationError(f"extracted file {rel} does not match its archive member")
