"""The archive on disk: a run saved once and kept, other results refused, the index, and saves that race."""

import dataclasses
import datetime as dt
import json
import multiprocessing
import os
import pickle
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import polars as pl
import pytest

import wequant.runs.archive
from tests.runs.engines import DESC, buggy, hold
from wequant.analytics import Metric, metrics_json
from wequant.data.files import atomic_write
from wequant.runs import FILES, INDEX, PROVENANCE, SAME, Archive, RunConflict, json_differences

ID = "019ec00a79bf60a2"
NEW_YEAR = "2026-01-01T00:00:00+00:00"
FOUND = {
    "daily.parquet": "equity row 2: 100194.9 → 100287.25; cash row 2: 89994.9 → 95187.25; sold row 2: 0.0 → 5200.0",
    "orders.parquet": "rows 1 → 2",
    "positions.parquet": "shares row 1: 1000.0 → 500.0; value row 1: 10200.0 → 5100.0",
}


def snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in sorted(directory.iterdir())}


def names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def save_hold(root: Path, shares: int) -> str:
    """Save a hold run of shares through a fresh Archive: one worker's job."""
    return Archive(root).save(hold(DESC | {"shares": shares}, 1))


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
    archive, run, bug = Archive(tmp_path), hold(DESC, 1), buggy(DESC, 1)
    archive.save(run)
    before = snapshot(tmp_path / ID)
    sale = bug.ledger.orders.select("value", "commission", "stamp_tax", "transfer_fee").row(1)
    assert sale == (5200.0, 5.0, 2.6, 0.05) and bug.id == ID
    assert bug.ledger.daily.select("cash", "equity").row(2) == (95187.25, 100287.25)
    found = archive.compare(ID, bug).differences
    assert sorted(found) == ["daily.parquet", "metrics.json", "orders.parquet", "positions.parquet"]
    assert {name: found[name] for name in FOUND} == FOUND
    metrics = json_differences(*(json.loads(metrics_json(r.metrics, r.spec)) for r in (run, bug)))
    assert metrics[0] == "metrics.annual_return.value: 0.26566728713318555 → 0.41492178353263176"
    assert found["metrics.json"].startswith(metrics[0])
    assert {
        "metrics.total_return.value: 0.0019489999999999785 → 0.0028725000000000556",
        "metrics.turnover.value: 6.050000000000001 → 9.180507120261826",
        "metrics.max_drawdown.value: 0.0029852261159520888 → 0.0020662740099246513",
    } <= set(metrics)
    with pytest.raises(RunConflict) as conflict:
        archive.save(bug)
    assert str(conflict.value) == (
        f"run {ID} is archived with other results (daily.parquet, metrics.json, orders.parquet, positions.parquet); "
        f"remove {tmp_path / ID} to replace it"
    )
    assert conflict.value.run_id == ID and conflict.value.differences == found
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


def test_index_of_a_missing_root(tmp_path):
    index = Archive(tmp_path / "runs").index()
    assert index.is_empty() and index.schema == pl.Schema(INDEX) and not (tmp_path / "runs").exists()


def test_index_rows(tmp_path, clock):
    archive = Archive(tmp_path)
    (tmp_path / ".x.tmp").mkdir()
    (tmp_path / "notes").write_text("not a run")
    clock(NEW_YEAR)
    run = hold(DESC, 1)
    archive.save(run)
    description = '{"cash":100000.0,"code":"600000.SH","shares":1000}'
    days = (dt.date(2024, 1, 2), dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    headline = (0.0019489999999999785, 0.26566728713318555, 2.722573368698643, 0.0029852261159520888)
    expected = (ID, "hold", 1, "fake", *days, description, *headline, run.code_version, NEW_YEAR)
    assert archive.index().rows() == [expected]
    clock("2026-01-02T00:00:00+00:00")
    double = hold(DESC | {"shares": 2000}, 1)
    archive.save(double)
    clock("2025-12-31T00:00:00+00:00")
    archive.save(hold(DESC | {"shares": 3000}, 1))
    index = archive.index()
    assert index["created_at"].is_sorted() and index["id"].to_list()[1:] == [ID, double.id]
    assert index.row(2, named=True)["total_return"] == 0.003937999999999997


def test_index_shows_null_for_a_metric_with_a_reason(tmp_path):
    run = hold(DESC, 1)
    no_sharpe = dataclasses.replace(run, metrics=run.metrics | {"sharpe": Metric(None, "ratio", "zero_volatility")})
    Archive(tmp_path).save(no_sharpe)
    row = Archive(tmp_path).index().row(0, named=True)
    assert row["sharpe"] is None and row["total_return"] == 0.0019489999999999785


def test_index_names_a_run_it_cannot_read(tmp_path):
    archive = Archive(tmp_path)
    archive.save(hold(DESC, 1))
    (tmp_path / ID / PROVENANCE).unlink()
    with pytest.raises(ValueError, match=re.escape(f"{tmp_path / ID} is not a readable run")):
        archive.index()
    (tmp_path / ID).rename(tmp_path / "0123456789abcdef")
    with pytest.raises(ValueError, match="0123456789abcdef is not a readable run"):
        archive.index()


@pytest.mark.parametrize("name", ["../x", "ABCDEF0123456789", "0123456789abcde", "0123456789abcdef0", 7, None])
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


def test_parallel_saves_of_the_same_runs(tmp_path):
    shares = [1000, 2000, 3000] * 12
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as pool:  # a fork pool can hang
        ids = list(pool.map(save_hold, [tmp_path] * len(shares), shares))
    assert ids[:3] == ids[3:6] and len(set(ids)) == 3 and names(tmp_path) == sorted(set(ids))


def test_the_default_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WEQUANT_HOME", str(tmp_path / "home"))
    archive = Archive()
    assert archive.root == tmp_path / "home" / "runs"
    assert archive.index().is_empty() and ID not in archive and not (tmp_path / "home").exists()
