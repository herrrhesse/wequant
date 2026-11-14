import json
import os
import threading
from pathlib import Path

import pytest

from wequant.data import files
from wequant.data.files import atomic_write


def test_an_interrupted_write_leaves_no_file(tmp_path, monkeypatch):
    """A file is complete or absent: the store and the raw layer rely on it."""
    target = tmp_path / "daily" / "2026" / "2026-11-06.parquet"

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write(target, b"data")
    assert not target.exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_a_replace_the_system_refuses_is_tried_again(tmp_path, monkeypatch):
    """Windows refuses to replace a file another handle has open; the write waits and tries again (W0)."""
    real, refusals = files.os.replace, iter([PermissionError, PermissionError])

    def refusing(src, dst):
        if (error := next(refusals, None)) is not None:
            raise error("in use")
        real(src, dst)

    monkeypatch.setattr(files.os, "replace", refusing)
    monkeypatch.setattr(files.time, "sleep", lambda seconds: None)
    atomic_write(tmp_path / "manifest.json", b"new")
    assert (tmp_path / "manifest.json").read_bytes() == b"new"


def test_a_file_kept_open_through_every_try_is_in_use_and_unchanged(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    target.write_bytes(b"old")

    def refused(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(files.os, "replace", refused)
    monkeypatch.setattr(files.time, "sleep", lambda seconds: None)
    with pytest.raises(files.FileInUse):
        atomic_write(target, b"new")
    assert target.read_bytes() == b"old" and list(tmp_path.rglob("*.tmp")) == []


def test_a_read_the_system_refuses_is_tried_again(tmp_path, monkeypatch):
    """Windows refuses to open a file while another thread replaces it; the read waits and tries again."""
    target, refusals = tmp_path / "manifest.json", iter([PermissionError, PermissionError])
    target.write_text('{"version": 3}', encoding="utf-8")
    real = Path.read_text

    def refusing(self, *args, **kwargs):
        if (error := next(refusals, None)) is not None:
            raise error("being replaced")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refusing)
    monkeypatch.setattr(files.time, "sleep", lambda seconds: None)
    assert files.read_text(target) == '{"version": 3}'


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to open a file while another thread replaces it")
def test_a_file_replaced_over_and_over_reads_whole_every_time(tmp_path):
    """One thread keeps replacing a file, as the store does its manifest; another keeps reading it, as a page's
    polls do: every read gets one whole version (the end-to-end runs' 500s on Windows)."""
    target, stop = tmp_path / "manifest.json", threading.Event()
    atomic_write(target, b'{"n": 0}')

    def write():
        n = 0
        while not stop.is_set():
            n += 1
            atomic_write(target, json.dumps({"n": n}).encode())

    writer = threading.Thread(target=write)
    writer.start()
    try:
        reads = [json.loads(files.read_text(target)) for _ in range(500)]
    finally:
        stop.set()
        writer.join()
    assert all(set(found) == {"n"} for found in reads)
