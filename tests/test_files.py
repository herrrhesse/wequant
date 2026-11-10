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
