"""What an account does with a holding at the open of its delisting day: write it down, cash it at its last close, or
hold it; never earlier than that day, which is only known when it comes."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import polars as pl

from wequant.account.execution import Execution

if TYPE_CHECKING:
    from wequant.panel.panel import Panel

TREATMENTS = ("write_down", "exit", "hold")
WRITE_DOWN, EXIT, HOLD = range(len(TREATMENTS))


class Exits(NamedTuple):
    """The holdings an account clears at the open of one row: columns, the share of the last close each returns as
    cash, and the TREATMENTS index of each."""

    cols: np.ndarray
    recovery: np.ndarray
    treatment: np.ndarray


def delisting_plan(panel: Panel, execution: Execution = Execution()) -> pl.DataFrame:
    """panel.delistings with the treatment a holding of each code gets under execution and the share of its last close
    it returns as cash (null when held).

    Without an official date a delisting is written down to zero under every rule but hold: last_date may come long
    before the official day and may only mark a suspension, so it never turns a holding into cash.
    """
    kind, mode = pl.col("kind"), execution.delisting
    held = pl.lit(mode == "hold") | (kind == "suspension")
    cashed = pl.lit(mode == "sell_at_last_close") | (pl.lit(mode == "auto") & (kind == "merger"))
    treatment = (
        pl.when(held)
        .then(pl.lit("hold"))
        .when(cashed & ~pl.col("approximate"))
        .then(pl.lit("exit"))
        .otherwise(pl.lit("write_down"))
    )
    recovery = (
        pl.when(treatment == "exit")
        .then(1.0)
        .when((treatment == "write_down") & ~pl.col("approximate"))
        .then(execution.failure_recovery)
        .when(treatment == "write_down")
        .then(0.0)
    )
    return panel.delistings.with_columns(treatment=treatment, recovery=recovery)


def exits(panel: Panel, execution: Execution) -> dict[int, Exits]:
    """The Exits of each panel row on which a delisting day opens with something to do."""
    plan = delisting_plan(panel, execution).filter((pl.col("treatment") != "hold") & pl.col("day").is_not_null())
    rows = np.searchsorted(panel.dates, plan["day"].to_numpy())
    inside = rows < len(panel.dates)
    inside[inside] = panel.dates[rows[inside]] == plan["day"].to_numpy()[inside]
    index = {code: j for j, code in enumerate(panel.codes.tolist())}
    cols = np.array([index[code] for code in plan["code"]], dtype=np.int64)
    treatment = plan["treatment"].replace_strict({name: k for k, name in enumerate(TREATMENTS)}, return_dtype=pl.Int8)
    recovery, treatment, found = plan["recovery"].to_numpy(), treatment.to_numpy(), {}
    for row in np.unique(rows[inside]).tolist():
        at = np.flatnonzero(inside & (rows == row))
        found[row] = Exits(cols[at], recovery[at], treatment[at])
    return found
