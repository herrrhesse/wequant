"""Re-running archived runs: the same code, a bug, another code version, and an old store version after a commit."""

import dataclasses
import json

import pytest

import wequant.account.account
from tests.account.golden import build_store
from tests.frames import table_frame
from tests.runs.engines import DESC, buggy, hold, store_engine
from wequant.runs import Archive, json_differences
from wequant.runs.record import run_json

ID = "0a001d6ea6cdea5c"
HELD = {"cash": 100000.0, "code": "600000.SH", "end": "2024-03-05", "qty": 1000}
CLOSE_UP = {  # version 2 of the golden store: 600000.SH closes at 10.40 instead of 10.30 on 2024-03-05
    "code": "600000.SH",
    "date": "2024-03-05",
    "open": 10200,
    "high": 10400,
    "low": 10100,
    "close": 10400,
    "preclose": 10100,
}


@pytest.fixture
def archive(tmp_path, monkeypatch) -> Archive:
    """An archive holding the hold run, made by a clean code version."""
    monkeypatch.setattr(wequant.account.account, "code_version", lambda: "0.1.0+gaaaaaaaaaaaa")
    archive = Archive(tmp_path / "runs")
    archive.save(hold(DESC, 1))
    return archive


def test_rerun_of_the_same_code_is_identical(archive):
    same = archive.rerun(ID, hold)
    assert same.identical and same.notes == () and same.run_id == same.run.id == ID


def test_rerun_finds_a_bug(archive):
    found = archive.rerun(ID, buggy).differences
    assert found == archive.compare(ID, buggy(DESC, 1)).differences
    assert found["orders.parquet"] == "rows 1 → 2" and "run.json" not in found


def test_another_or_dirty_code_version_is_noted_never_refused(archive):
    def dirty(description: dict, version: int):
        run = hold(description, version)
        meta = run.ledger.meta | {"code_version": "0.1.0+gbbbbbbbbbbbb.dirty"}
        return dataclasses.replace(run, ledger=dataclasses.replace(run.ledger, meta=meta))

    comparison = archive.rerun(ID, dirty)
    assert comparison.identical
    assert comparison.notes == (
        "archived with code 0.1.0+gaaaaaaaaaaaa, re-run with 0.1.0+gbbbbbbbbbbbb.dirty",
        "code 0.1.0+gbbbbbbbbbbbb.dirty had uncommitted changes",
    )


def test_an_old_store_version_reruns_after_a_commit(tmp_path):
    store = build_store(tmp_path / "home")
    engine, archive = store_engine(store, tmp_path / "cache"), Archive(tmp_path / "runs")
    old = engine(HELD, 1)
    assert old.ledger.daily["equity"].to_list() == [100000.0, 100094.9, 100294.9]
    assert old.metrics["total_return"].value == 0.0029489999999998684
    assert old.metrics["sharpe"].value == 30.900795336036996
    run_id = archive.save(old)
    constants = {"volume": 1_000_000, "amount": 1e7, "preclose_derived": False, "source": "test"}
    store.commit({"source": "test"}, upsert={"bars": table_frame("bars", [CLOSE_UP], **constants)})
    assert archive.rerun(run_id, engine).identical
    new = engine(HELD, 2)
    assert new.id != run_id and new.ledger.daily["equity"].to_list() == [100000.0, 100094.9, 100394.9]
    found = archive.compare(run_id, new).differences
    assert sorted(found) == ["daily.parquet", "metrics.json", "positions.parquet", "run.json"]
    assert found["daily.parquet"] == "equity row 2: 100294.9 → 100394.9"
    assert found["positions.parquet"] == "value row 1: 10300.0 → 10400.0"
    changed = json_differences(json.loads(run_json(old)), json.loads(run_json(new)))
    assert "inputs.store_version: 1 → 2" in changed
    assert any(item.startswith("inputs.panel_key: ") for item in changed)
    assert any(item.startswith("id: ") for item in changed)
