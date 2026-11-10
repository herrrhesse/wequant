"""A run as the archive's bytes: the hold run's goldens, what the bytes depend on, and reading a run back."""

import dataclasses
import datetime as dt
import json

import polars as pl
import pytest

from tests.runs.engines import DESC, hold
from wequant.account import EXECUTION_VERSION
from wequant.backtest import WARNINGS
from wequant.runs import FILES, FORMAT, Archive, document, provenance, read_run, run_files
from wequant.runs.record import run_json

ID = "0a001d6ea6cdea5c"
INPUTS = {
    "format": FORMAT,
    "engine": "hold",
    "description": DESC,
    "store_version": 1,
    "data_key": "fake",
    "rules_key": "fake-r1",
    "panel_key": "fake-fake-r1-2024-01-02-2024-01-04-56dab30ebda9",
    "versions": {"hold": 1},
}


def test_document_golden():
    text = '{\n "a": [\n  1.0,\n  1e-07,\n  null,\n  "中"\n ],\n "b": 1\n}\n'
    assert document({"b": 1, "a": [1.0, 1e-07, None, "中"]}) == text.encode()


def test_the_hold_run_golden():
    """The run id is the digest of the inputs that decide the run, golden for the hold run."""
    run = hold(DESC, 1)
    assert run.inputs == INPUTS and run.id == ID


def test_run_json_golden():
    text = run_json(hold(DESC, 1))
    assert text.startswith(b'{\n "anchor": "2024-01-02",\n "end": "2024-01-04",\n "id": "0a001d6ea6cdea5c",')
    ledger = {
        "cash": 100000.0,
        "data_key": "fake",
        "execution": {
            "commission_rate": 0.0003,
            "delisting": "auto",
            "failure_recovery": 0.0,
            "min_commission": 5.0,
            "price": "open",
            "slippage_bps": 0.0,
        },
        "execution_version": EXECUTION_VERSION,
        "rules_key": "fake-r1",
        "start": "2024-01-02",
        "store_version": 1,
    }
    assert json.loads(text) == {
        "anchor": "2024-01-02",
        "end": "2024-01-04",
        "id": ID,
        "inputs": INPUTS,
        "ledger": ledger,
        "panel_key": INPUTS["panel_key"],
    }


def test_compared_files_hold_neither_the_clock_nor_the_code_version(clock):
    run = hold(DESC, 1)
    first, stamp = run_files(run), provenance(run)
    clock("2027-06-30T12:34:56+00:00")
    assert run_files(run) == first and provenance(run) != stamp
    for data in first.values():
        assert b"2027-06-30" not in data and run.code_version.encode() not in data
    made = json.loads(provenance(run))
    assert made["created_at"] == "2027-06-30T12:34:56+00:00" and made["code_version"] == run.code_version
    assert sorted(made) == ["code_version", "created_at", "numba", "numpy", "polars", "python"]


def test_run_files_ignore_how_frames_are_chunked():
    run = hold(DESC, 1)
    ledger = run.ledger
    pieces = {
        name: pl.concat([frame[i : i + 1] for i in range(frame.height)], rechunk=False)
        for name, frame in {"daily": ledger.daily, "positions": ledger.positions, "orders": ledger.orders}.items()
    }
    assert pieces["daily"].n_chunks() == 3
    chunked = dataclasses.replace(run, ledger=dataclasses.replace(ledger, **pieces))
    assert run_files(chunked) == run_files(run)


def test_read_run_round_trip(tmp_path):
    """A run read back from its directory writes the same bytes: frames with their dtypes, metrics, warnings in the
    engine's order with their params, and the code version from provenance.json."""
    detail = pl.DataFrame(
        {
            "date": [dt.date(2024, 1, 2)] * 2,
            "code": ["600000.SH", "000001.SZ"],
            "cheap": [True, None],
            "rank": [1, None],
        },
        schema={"date": pl.Date, "code": pl.String, "cheap": pl.Boolean, "rank": pl.Int64},
    )
    warnings = {code: ("text", {"k": k, "day": None}) for k, code in enumerate(WARNINGS)}  # the engine's order
    run = dataclasses.replace(hold(DESC, 1), detail=detail, warnings=warnings)
    Archive(tmp_path).save(run)
    loaded = read_run(tmp_path / run.id)
    assert list(loaded.warnings.items()) == list(warnings.items())
    assert run_files(loaded) == {name: (tmp_path / run.id / name).read_bytes() for name in FILES}
    assert loaded.ledger.meta == run.ledger.meta and loaded.code_version == run.code_version
    assert loaded.metrics == run.metrics and loaded.spec == run.spec
    assert loaded.metrics["max_drawdown_peak"].value == dt.date(2024, 1, 3)
    assert loaded.detail.schema == detail.schema and loaded.detail.equals(detail)
    assert (loaded.id, loaded.anchor, loaded.description, loaded.warnings) == (run.id, run.anchor, DESC, run.warnings)


def test_read_run_refuses_another_format_or_another_runs_directory(tmp_path):
    """0802fa7: a run of another FORMAT, or in a directory named after another id, is refused, never misread."""
    run = hold(DESC, 1)
    Archive(tmp_path).save(run)
    path = tmp_path / run.id / "run.json"
    doc = json.loads(path.read_bytes())
    path.write_bytes(document(doc | {"inputs": doc["inputs"] | {"format": FORMAT + 1}}))
    with pytest.raises(ValueError, match=f"format {FORMAT + 1}, not {FORMAT}"):
        read_run(tmp_path / run.id)
    path.write_bytes(document(doc))
    (tmp_path / run.id).rename(tmp_path / "0123456789abcdef")
    with pytest.raises(ValueError, match=f"run.json names {run.id}"):
        read_run(tmp_path / "0123456789abcdef")
