"""data_issues rows, with one JSON encoding of detail for every writer."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence

import polars as pl

from wequant.data.schema import TABLES


def issue_rows(
    rows: pl.DataFrame, table: str, kind: str, action: str, detail: Sequence[str], source: str
) -> pl.DataFrame:
    """data_issues rows for rows' (code, date); the detail columns become one JSON object per row."""
    details = [encode(d) for d in rows.select(detail).to_dicts()] if detail else [None] * rows.height
    frame = rows.select("code", "date").with_columns(
        table=pl.lit(table),
        kind=pl.lit(kind),
        action=pl.lit(action),
        detail=pl.Series(details, dtype=pl.String),
        source=pl.lit(source),
    )
    frame = frame.select(list(TABLES["data_issues"].schema))
    TABLES["data_issues"].validate(frame)
    return frame


def encode(detail: dict) -> str:
    """A JSON object with sorted keys, as the seed import writes it; dates become ISO strings, NaN raises."""
    return json.dumps(detail, sort_keys=True, ensure_ascii=False, allow_nan=False, default=_iso)


def _iso(value: object) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} cannot go into an issue detail")
