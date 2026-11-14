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
