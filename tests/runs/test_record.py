"""A run as the archive's bytes: the hold run's goldens, what the bytes depend on, and reading a run back."""

import dataclasses
import datetime as dt
import json

import polars as pl
import pytest

from tests.runs.engines import DESC, hold
from wequant.account import EXECUTION_VERSION
from wequant.runs import FILES, FORMAT, PROVENANCE, Archive, document, provenance, read_run, run_files
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
    run = hold(DESC, 1)
    daily = run.ledger.daily.select("date", "equity", "cash", "bought", "sold").rows()
    assert daily == [
        (dt.date(2024, 1, 2), 100000.0, 100000.0, 0.0, 0.0),
        (dt.date(2024, 1, 3), 100494.9, 89994.9, 10000.0, 0.0),
        (dt.date(2024, 1, 4), 100194.9, 89994.9, 0.0, 0.0),
    ]
    assert run.ledger.positions.select("date", "code", "shares", "value").rows() == [
        (dt.date(2024, 1, 3), "600000.SH", 1000.0, 10500.0),
        (dt.date(2024, 1, 4), "600000.SH", 1000.0, 10200.0),
    ]
    costs = ("price", "value", "commission", "stamp_tax", "transfer_fee")
    assert run.ledger.orders.select(costs).rows() == [(10.0, 10000.0, 5.0, 0.0, 0.1)]
    m = {key: metric.value for key, metric in run.metrics.items()}
    assert m["trading_days"] == 2 and m["total_return"] == 0.0019489999999999785
    assert m["annual_return"] == 0.26566728713318555 and m["annual_volatility"] == 0.08727648727547277
    assert m["sharpe"] == 2.722573368698643 and m["max_drawdown"] == 0.0029852261159520888
    assert m["max_drawdown"] == pytest.approx(300 / 100494.9, rel=1e-12)
    assert m["turnover"] == 6.050000000000001 and m["avg_holding_days"] == 2.0 and m["executable_order_share"] == 1.0
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


def test_files_come_in_files_order():
    assert FILES == (
        "run.json",
        "metrics.json",
        "warnings.json",
        "daily.parquet",
        "positions.parquet",
        "orders.parquet",
        "delisted.parquet",
        "picks.parquet",
        "funnel.parquet",
        "detail.parquet",
    )
    assert tuple(run_files(hold(DESC, 1))) == FILES and PROVENANCE not in FILES


def test_warnings_keep_the_engine_order_and_their_params(tmp_path):
    assert run_files(hold(DESC, 1))["warnings.json"] == b"[]\n"
    warnings = {"st_unknown": ("x", {"known_share": 0.5}), "limits_unchecked": ("y", {})}
    run = dataclasses.replace(hold(DESC, 1), warnings=warnings)
    text = '[\n [\n  "st_unknown",\n  "x",\n  {\n   "known_share": 0.5\n  }\n ],\n [\n  "limits_unchecked",\n  "y",\n  {}\n ]\n]\n'
    assert run_files(run)["warnings.json"] == text.encode()
    Archive(tmp_path).save(run)
    assert list(read_run(tmp_path / run.id).warnings.items()) == list(warnings.items())


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
    detail = pl.DataFrame(
        {
            "date": [dt.date(2024, 1, 2)] * 2,
            "code": ["600000.SH", "000001.SZ"],
            "cheap": [True, None],
            "rank": [1, None],
        },
        schema={"date": pl.Date, "code": pl.String, "cheap": pl.Boolean, "rank": pl.Int64},
    )
    run = dataclasses.replace(hold(DESC, 1), detail=detail, warnings={"no_picks": ("none", {"day": None})})
    Archive(tmp_path).save(run)
    loaded = read_run(tmp_path / run.id)
    assert run_files(loaded) == {name: (tmp_path / run.id / name).read_bytes() for name in FILES}
    assert loaded.ledger.meta == run.ledger.meta and loaded.code_version == run.code_version
    assert loaded.metrics == run.metrics and loaded.spec == run.spec
    assert loaded.metrics["max_drawdown_peak"].value == dt.date(2024, 1, 3)
    assert loaded.detail.schema == detail.schema and loaded.detail.equals(detail)
    assert (loaded.id, loaded.anchor, loaded.description, loaded.warnings) == (run.id, run.anchor, DESC, run.warnings)


def test_read_run_refuses_another_format(tmp_path):
    run = hold(DESC, 1)
    Archive(tmp_path).save(run)
    path = tmp_path / run.id / "run.json"
    doc = json.loads(path.read_bytes())
    doc["inputs"]["format"] = FORMAT + 1
    path.write_bytes(document(doc))
    with pytest.raises(ValueError, match=f"format {FORMAT + 1}, not {FORMAT}"):
        read_run(tmp_path / run.id)


def test_read_run_refuses_a_directory_named_after_another_run(tmp_path):
    run = hold(DESC, 1)
    Archive(tmp_path).save(run)
    (tmp_path / run.id).rename(tmp_path / "0123456789abcdef")
    with pytest.raises(ValueError, match=f"run.json names {run.id}"):
        read_run(tmp_path / "0123456789abcdef")
