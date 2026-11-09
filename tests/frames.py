"""Store table frames for tests, built from row dicts."""

from __future__ import annotations

import polars as pl

from wequant.data.schema import TABLES


def table_frame(name: str, rows: list[dict], **constants) -> pl.DataFrame:
    """A valid frame of store table name from row dicts (ISO strings allowed for dates); constants fill whole columns, null elsewhere."""
    table = TABLES[name]
    if not rows:
        return pl.DataFrame(schema=table.schema)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame = frame.with_columns(**{column: pl.lit(value) for column, value in constants.items()})
    frame = frame.with_columns(pl.lit(None).alias(c) for c in table.schema if c not in frame.columns)
    frame = frame.select(_typed(frame, c, dtype) for c, dtype in table.schema.items())
    table.validate(frame)
    return frame


def _typed(frame: pl.DataFrame, column: str, dtype: pl.DataType) -> pl.Expr:
    if dtype == pl.Date and frame.schema[column] == pl.String:
        return pl.col(column).str.to_date()
    return pl.col(column).cast(dtype)
