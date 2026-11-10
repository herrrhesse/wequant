"""The anomalous cells of a panel: data_issues rows that taint a (date, code) cell a run reads, by ISSUE_KINDS."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.data.schema import ISSUE_KINDS, LEVELS, NOT_ANOMALOUS_ACTIONS

if TYPE_CHECKING:
    from wequant.panel.cache import PanelCache

ANOMALIES = {
    "row": pl.Int64,  # panel row and column of the cell
    "col": pl.Int64,
    "date": pl.Date,
    "code": pl.String,
    "kind": pl.String,
    "class": pl.String,  # ISSUE_KINDS' anomaly class
    "level": pl.String,
    "excluded": pl.Boolean,  # the panel reads the cell's day as missing data
}
EXCLUDABLE = ("price", "volume", "bar", "limits", "status")  # what reading the day as missing removes
_TABLES = ("bars", "adj_factor", "daily_status")


def anomaly_cells(src: PanelCache, dates: np.ndarray, codes: np.ndarray, excluded: tuple[str, ...]) -> pl.DataFrame:
    """ANOMALIES on the grid of dates x codes; a day that is not a trading day counts on the next one. ValueError for
    an anomaly-table row whose kind ISSUE_KINDS does not classify."""
    issues = (
        src.scan(
            "data_issues",
            columns=["table", "code", "date", "kind", "action"],
            start=dates[0].item(),
            end=dates[-1].item(),
        )
        .filter(pl.col("table").is_in(_TABLES), ~pl.col("action").is_in(NOT_ANOMALOUS_ACTIONS))
        .collect()
    )
    kinds = pl.DataFrame(
        [(name, kind.anomaly, kind.level) for name, kind in ISSUE_KINDS.items() if kind.anomaly is not None],
        schema={"kind": pl.String, "class": pl.String, "level": pl.String},
        orient="row",
    )
    unknown = issues.join(kinds, on="kind", how="anti")["kind"].unique().sort().to_list()
    if unknown:
        raise ValueError(f"data_issues has anomaly rows of kinds ISSUE_KINDS does not classify: {unknown}")
    rows = np.searchsorted(dates, issues["date"].to_numpy())
    index = {code: j for j, code in enumerate(codes.tolist())}
    cols = np.array([index.get(code, -1) for code in issues["code"]], dtype=np.int64)
    cells = issues.with_columns(row=rows, col=cols).filter((pl.col("row") < len(dates)) & (pl.col("col") >= 0))
    cells = cells.with_columns(date=pl.Series(dates).gather(cells["row"])).join(kinds, on="kind")
    out = pl.col("level").is_in(list(excluded)) & pl.col("class").is_in(EXCLUDABLE)
    return cells.select(*list(ANOMALIES)[:-1], excluded=out).sort("row", "col", "kind").cast(ANOMALIES)


def exclusion(exclude: tuple[str, ...]) -> tuple[str, ...]:
    """The levels a panel reads as missing, in LEVELS order; ValueError for one that is not a level."""
    unknown = set(exclude) - set(LEVELS)
    if unknown:
        raise ValueError(f"{sorted(unknown)} are not anomaly levels {LEVELS}")
    return tuple(level for level in LEVELS if level in exclude)
