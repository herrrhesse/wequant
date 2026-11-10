"""Panel cells as result rows: one per (date, code), with its day status and null for NaN or infinity."""

from __future__ import annotations

import datetime as dt
from typing import Literal, get_args

import numpy as np

from wequant.panel import Panel
from wequant.service.base import Model

Status = Literal["not_listed", "traded", "suspended", "missing"]
STATUS_NAMES = np.array(get_args(Status))  # indexed by wequant.panel's NOT_LISTED, TRADED, SUSPENDED, MISSING


class Row(Model):
    """One (date, code) cell; a value is null where the panel holds NaN or infinity."""

    date: dt.date
    code: str
    status: Status
    """The descriptive day status: a later bar can change it."""
    values: dict[str, float | None]


def rows(panel: Panel, grids: dict[str, np.ndarray], first: int = 0) -> list[dict]:
    """Row dicts of every cell from row first on, code by code in panel order, each by date."""
    dates = panel.dates[first:].tolist()
    status = STATUS_NAMES[panel.status[first:]].T.tolist()
    columns = {name: _cells(grid[first:]) for name, grid in grids.items()}
    return [
        {"date": day, "code": code, "status": status[n][t], "values": {k: c[n][t] for k, c in columns.items()}}
        for n, code in enumerate(panel.codes.tolist())
        for t, day in enumerate(dates)
    ]


def _cells(grid: np.ndarray) -> list[list]:
    """grid as code-major lists of floats, None where it is not finite."""
    cells = grid.astype(object)
    cells[~np.isfinite(grid)] = None
    return cells.T.tolist()
