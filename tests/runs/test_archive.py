"""The archive on disk: a run saved once and kept, other results refused, the index, and saves that race."""

import dataclasses
import datetime as dt
import os
import pickle
import re
from pathlib import Path

import polars as pl
import pytest

import wequant.runs.archive
from tests.runs.engines import DESC, buggy, hold
from wequant.analytics import Metric
from wequant.data.files import atomic_write
from wequant.runs import FILES, INDEX, PROVENANCE, SAME, Archive, RunConflict

ID = "0a001d6ea6cdea5c"
NEW_YEAR = "2026-01-01T00:00:00+00:00"


def snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in sorted(directory.iterdir())}


def names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_save_and_save_again(tmp_path, clock):
    archive = Archive(tmp_path / "runs")
    clock(NEW_YEAR)
    assert archive.save(hold(DESC, 1)) == ID
    assert names(archive.root) == [ID] and names(archive.root / ID) == sorted([*FILES, PROVENANCE])
    before = snapshot(archive.root / ID)
    clock("2026-01-02T00:00:00+00:00")
    assert archive.save(hold(DESC, 1)) == ID
    assert snapshot(archive.root / ID) == before and names(archive.root) == [ID]


def test_a_result_changing_bug_conflicts(tmp_path):
    """A run whose id is archived with other results raises RunConflict naming the files that differ, leaves the
    archive as it was, and pickles across processes; the difference texts are test_compare's."""
    archive, bug = Archive(tmp_path), buggy(DESC, 1)
    archive.save(hold(DESC, 1))
    before = snapshot(tmp_path / ID)
    assert bug.id == ID
    with pytest.raises(RunConflict) as conflict:
        archive.save(bug)
    assert sorted(conflict.value.differences) == [
        "daily.parquet",
        "metrics.json",
        "orders.parquet",
        "positions.parquet",
    ]
    assert conflict.value.run_id == ID and conflict.value.differences == archive.compare(ID, bug).differences
    assert str(pickle.loads(pickle.dumps(conflict.value))) == str(conflict.value)
    assert snapshot(tmp_path / ID) == before


def test_the_same_content_in_other_bytes_is_kept(tmp_path):
    archive, run = Archive(tmp_path), hold(DESC, 1)
    archive.save(run)
    path = tmp_path / ID / "daily.parquet"
    run.ledger.daily.write_parquet(path, compression="lz4")
    lz4 = path.read_bytes()
    assert archive.save(hold(DESC, 1)) == ID and path.read_bytes() == lz4
    assert archive.compare(ID, run).differences == {"daily.parquet": SAME}


def test_index_rows(tmp_path, clock):
    """One row per archived run by created_at then id, from its files; other entries in the root are skipped."""
    archive = Archive(tmp_path)
    (tmp_path / ".x.tmp").mkdir()
    (tmp_path / "notes").write_text("not a run")
    clock(NEW_YEAR)
    run = hold(DESC, 1)
    archive.save(run)
    description = '{"cash":100000.0,"code":"600000.SH","shares":1000}'
    days = (dt.date(2024, 1, 2), dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    headline = (0.0019489999999999785, 0.26566728713318555, 2.722573368698643, 0.0029852261159520888)
    expected = (ID, "hold", 1, "fake", *days, description, *headline, run.code_version, NEW_YEAR, None)
    assert archive.index().rows() == [expected]
    clock("2026-01-02T00:00:00+00:00")
    double = hold(DESC | {"shares": 2000}, 1)
    archive.save(double)
    clock("2025-12-31T00:00:00+00:00")
    archive.save(hold(DESC | {"shares": 3000}, 1))
    index = archive.index()
    assert index["created_at"].is_sorted() and index["id"].to_list()[1:] == [ID, double.id]
    assert index.row(2, named=True)["total_return"] == 0.003937999999999997
    no_sharpe = dataclasses.replace(run, metrics=run.metrics | {"sharpe": Metric(None, "ratio", "zero_volatility")})
    Archive(tmp_path / "other").save(no_sharpe)
    row = Archive(tmp_path / "other").index().row(0, named=True)
    assert row["sharpe"] is None and row["total_return"] == headline[0]  # a metric with a reason shows as null


def test_index_names_a_run_it_cannot_read(tmp_path):
    archive = Archive(tmp_path)
    archive.save(hold(DESC, 1))
    (tmp_path / ID / PROVENANCE).unlink()
    with pytest.raises(ValueError, match=re.escape(f"{tmp_path / ID} is not a readable run")):
        archive.index()
    (tmp_path / ID).rename(tmp_path / "0123456789abcdef")
    with pytest.raises(ValueError, match="0123456789abcdef is not a readable run"):
        archive.index()


@pytest.mark.parametrize("name", ["../x", "0123456789abcdef0"], ids=["path_traversal", "17_characters"])
def test_names_that_are_not_run_ids(tmp_path, name):
    archive = Archive(tmp_path)
    with pytest.raises(ValueError, match="is not a run id"):
        archive.path(name)
    assert name not in archive


def test_unknown_runs(tmp_path):
    archive = Archive(tmp_path)
    with pytest.raises(KeyError, match="no run 0123456789abcdef"):
        archive.load("0123456789abcdef")
    with pytest.raises(KeyError):
        archive.compare("0123456789abcdef", hold(DESC, 1))
    assert "0123456789abcdef" not in archive and ID not in archive
    archive.save(hold(DESC, 1))
    assert ID in archive and archive.path(ID) == tmp_path / ID


def test_another_process_wins_the_rename(tmp_path, monkeypatch):
    real, lost = os.rename, []

    def rename(src, dst):
        monkeypatch.setattr(os, "rename", real)
        Archive(tmp_path).save(hold(DESC, 1))
        try:
            real(src, dst)
        except OSError:
            lost.append(dst)
            raise

    monkeypatch.setattr(os, "rename", rename)
    assert Archive(tmp_path).save(hold(DESC, 1)) == ID
    assert lost == [tmp_path / ID] and names(tmp_path) == [ID]


def test_a_failed_write_leaves_nothing(tmp_path, monkeypatch):
    written = []

    def write(path, data):
        if len(written) == 2:
            raise OSError("disk full")
        written.append(path.name)
        atomic_write(path, data)

    monkeypatch.setattr(wequant.runs.archive, "atomic_write", write)
    with pytest.raises(OSError, match="disk full"):
        Archive(tmp_path).save(hold(DESC, 1))
    assert written == ["run.json", "metrics.json"] and names(tmp_path) == []


def test_the_default_root(tmp_path, monkeypatch):
    """The archive defaults to $WEQUANT_HOME/runs; reading a root that does not exist finds nothing and creates nothing."""
    monkeypatch.setenv("WEQUANT_HOME", str(tmp_path / "home"))
    archive = Archive()
    assert archive.root == tmp_path / "home" / "runs"
    index = archive.index()
    assert index.is_empty() and index.schema == pl.Schema(INDEX)
    assert ID not in archive and not (tmp_path / "home").exists()
