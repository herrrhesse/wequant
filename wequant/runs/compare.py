"""How a fresh run's bytes differ from an archived run's, in words a report can show."""

from __future__ import annotations

import datetime as dt
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from wequant.runs.run import Run

SHOWN = 3  # differences named per file
SAME = "same content, different bytes"


class _Absent:
    """The side of a JSON comparison that lacks a key."""

    def __repr__(self) -> str:
        return "absent"


_ABSENT = _Absent()


@dataclass(frozen=True)
class Comparison:
    """A run against an archived run: the files whose bytes differ, and notes on the code that made each."""

    run_id: str
    run: Run
    differences: dict[str, str]  # file name -> what differs in it, SAME when only the bytes do
    notes: tuple[str, ...]

    @property
    def identical(self) -> bool:
        """Every compared file is byte-identical."""
        return not self.differences


def differences(old: Mapping[str, bytes], new: Mapping[str, bytes]) -> dict[str, str]:
    """For each file whose bytes differ, by name: what differs in it."""
    names = sorted(old.keys() | new.keys())
    return {name: _file_text(name, old.get(name), new.get(name)) for name in names if old.get(name) != new.get(name)}


def json_differences(old: object, new: object, path: str = "") -> list[str]:
    """Each leaf whose JSON text differs, as "path: old → new"; 1 and 1.0, or 0.0 and -0.0, differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        keys = sorted(old.keys() | new.keys())
        return [d for k in keys for d in json_differences(old.get(k, _ABSENT), new.get(k, _ABSENT), _key(path, k))]
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [d for i, pair in enumerate(zip(old, new)) for d in json_differences(*pair, f"{path}[{i}]")]
    if _ABSENT not in (old, new) and json.dumps(old) == json.dumps(new):
        return []
    return [f"{path}: {old!r} → {new!r}" if path else f"{old!r} → {new!r}"]


def frame_differences(old: pl.DataFrame, new: pl.DataFrame) -> list[str]:
    """The schema if it differs, else the row count, else each differing column's first row and how many more."""
    if old.schema != new.schema:
        return [f"columns {list(old.schema.items())} → {list(new.schema.items())}"]
    if old.height != new.height:
        return [f"rows {old.height:,} → {new.height:,}"]
    return [_column_text(old[c], new[c]) for c in old.columns if old[c].ne_missing(new[c]).any()]


def code_notes(then: str, now: str) -> tuple[str, ...]:
    """A note when the archived and re-run code versions differ, and one per version with uncommitted changes."""
    changed = (f"archived with code {then}, re-run with {now}",) if then != now else ()
    dirty = tuple(f"code {v} had uncommitted changes" for v in dict.fromkeys((then, now)) if v.endswith(".dirty"))
    return changed + dirty


def _file_text(name: str, old: bytes | None, new: bytes | None) -> str:
    if old is None:
        return "not archived"
    if new is None:
        return "not produced by the re-run"
    if name.endswith(".json"):
        found = json_differences(json.loads(old), json.loads(new))
    else:
        found = frame_differences(pl.read_parquet(io.BytesIO(old)), pl.read_parquet(io.BytesIO(new)))
    if not found:
        return SAME
    more = f" (+{len(found) - SHOWN} more)" if len(found) > SHOWN else ""
    return "; ".join(found[:SHOWN]) + more


def _column_text(old: pl.Series, new: pl.Series) -> str:
    differs = old.ne_missing(new)
    i, more = differs.arg_true()[0], differs.sum() - 1
    text = f"{old.name} row {i:,}: {_show(old[i])} → {_show(new[i])}"
    return text + (f" and {more:,} more rows" if more else "")


def _show(value: object) -> str:
    return value.isoformat() if isinstance(value, dt.date) else repr(value)


def _key(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
