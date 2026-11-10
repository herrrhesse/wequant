"""A run as the bytes the archive keeps, and back."""

from __future__ import annotations

import datetime as dt
import json
import platform
from importlib.metadata import version
from pathlib import Path

import polars as pl

from wequant.account import Ledger
from wequant.analytics import Metric, MetricSpec, metrics_json
from wequant.data.files import parquet_bytes
from wequant.runs.run import FORMAT, Run

FRAMES = ("daily", "positions", "orders", "picks", "funnel", "detail")
FILES = ("run.json", "metrics.json", "warnings.json", *(f"{name}.parquet" for name in FRAMES))  # compared on rerun
PROVENANCE = "provenance.json"  # code version, library versions, creation time: never compared
LIBRARIES = ("numpy", "polars", "numba")


def document(value: object) -> bytes:
    """JSON bytes that depend only on the content: sorted keys, indent 1, no NaN, a final newline."""
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False).encode() + b"\n"


def run_json(run: Run) -> bytes:
    """What decided the run and what it ran on: id, inputs, panel_key, anchor, end, ledger meta without code_version."""
    meta = {key: value for key, value in run.ledger.meta.items() if key != "code_version"}
    return document(
        {
            "id": run.id,
            "inputs": run.inputs,
            "panel_key": run.panel_key,
            "anchor": run.anchor.isoformat(),
            "end": run.ledger.daily["date"][-1].isoformat(),
            "ledger": meta,
        }
    )


def run_files(run: Run) -> dict[str, bytes]:
    """The compared files in FILES order: equal runs give equal bytes."""
    ledger = run.ledger
    frames = (ledger.daily, ledger.positions, ledger.orders, run.picks, run.funnel, run.detail)
    return {
        "run.json": run_json(run),
        "metrics.json": metrics_json(run.metrics, run.spec),
        "warnings.json": document([[code, text] for code, text in run.warnings.items()]),
        **{f"{name}.parquet": parquet_bytes(frame) for name, frame in zip(FRAMES, frames)},
    }


def now() -> str:
    """UTC now as ISO text to the second: the archive's only clock."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def provenance(run: Run) -> bytes:
    """provenance.json: code_version, created_at, python, numpy, polars, numba."""
    made = {"code_version": run.code_version, "created_at": now(), "python": platform.python_version()}
    return document(made | {name: version(name) for name in LIBRARIES})


def read_run(directory: Path) -> Run:
    """The run archived in directory; ValueError for another FORMAT or an id that is not the directory's."""
    directory = Path(directory)
    doc = read_json(directory / "run.json")
    inputs = doc["inputs"]
    if inputs["format"] != FORMAT:
        raise ValueError(f"{directory} holds a run of format {inputs['format']}, not {FORMAT}")
    daily, positions, orders, picks, funnel, detail = (pl.read_parquet(directory / f"{n}.parquet") for n in FRAMES)
    meta = doc["ledger"] | {"code_version": read_json(directory / PROVENANCE)["code_version"]}
    scored = read_json(directory / "metrics.json")
    run = Run(
        engine=inputs["engine"],
        description=inputs["description"],
        versions=inputs["versions"],
        panel_key=doc["panel_key"],
        anchor=dt.date.fromisoformat(doc["anchor"]),
        ledger=Ledger(daily, positions, orders, meta),
        metrics={key: _metric(**metric) for key, metric in scored["metrics"].items()},
        spec=MetricSpec(**scored["spec"]),
        picks=picks,
        funnel=funnel,
        detail=detail,
        warnings=dict(read_json(directory / "warnings.json")),
    )
    if not run.id == doc["id"] == directory.name:
        raise ValueError(f"{directory} is not the run it holds: run.json names {doc['id']}, its inputs {run.id}")
    return run


def read_json(path: Path) -> object:
    """The JSON document at path."""
    return json.loads(path.read_bytes())


def _metric(value: object, unit: str, reason: str | None = None) -> Metric:
    return Metric(dt.date.fromisoformat(value) if unit == "date" and value is not None else value, unit, reason)
