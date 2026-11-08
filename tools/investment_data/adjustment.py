"""Back-adjustment ratio (adjclose / raw close) as a piecewise-constant series per code."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_STEP = 1e-4  # smallest ratio change recorded as an adjustment event
BLIP_RETURN = 1e-3  # a one-day jump whose neighbours agree within this is a source glitch


@dataclass
class Ratio:
    row_ratio: np.ndarray  # recorded ratio of every row
    event_rows: np.ndarray  # rows where a recorded ratio starts, including each code's first row
    run_starts: np.ndarray
    run_values: np.ndarray
    blip_runs: np.ndarray
    merged_runs: np.ndarray  # runs whose change from the recorded ratio was below MIN_STEP

    @property
    def event_ratio(self) -> np.ndarray:
        return self.row_ratio[self.event_rows]


def runs(code_idx: np.ndarray, close: np.ndarray, adjclose: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split each code's rows into runs that one ratio can explain.

    adjclose is rounded to 0.01 at the source, so each day only bounds the ratio to
    [(adjclose - tol) / close, (adjclose + tol) / close]. A run lasts while those bounds
    still intersect. Returns each run's first row and the midpoint of its bounds.
    """
    tol = 0.005 + np.spacing(adjclose.astype(np.float32)).astype(np.float64)
    lo = (adjclose - tol) / close
    hi = (adjclose + tol) / close
    starts, values = [], []
    code_starts = np.flatnonzero(np.r_[True, code_idx[1:] != code_idx[:-1]])
    for s, e in zip(code_starts.tolist(), np.r_[code_starts[1:], code_idx.size].tolist()):
        i = s
        while i < e:
            width = 256
            while True:
                stop = min(e, i + width)
                run_lo = np.maximum.accumulate(lo[i:stop])
                run_hi = np.minimum.accumulate(hi[i:stop])
                breaks = np.flatnonzero(run_lo > run_hi)
                if breaks.size or stop == e:
                    break
                width *= 4
            j = i + int(breaks[0]) if breaks.size else stop
            starts.append(i)
            values.append((run_lo[j - i - 1] + run_hi[j - i - 1]) / 2)
            i = j
    return np.array(starts, dtype=np.int64), np.array(values)


def ratio(code_idx: np.ndarray, close: np.ndarray, adjclose: np.ndarray) -> Ratio:
    """Recorded ratio per row: one-day blips are ignored and changes below MIN_STEP are merged."""
    starts, values = runs(code_idx, close, adjclose.astype(np.float64))
    n = starts.size
    lengths = np.diff(np.r_[starts, code_idx.size])
    run_code = code_idx[starts]
    first = np.r_[True, run_code[1:] != run_code[:-1]]
    last = np.r_[run_code[1:] != run_code[:-1], True]
    blip = np.zeros(n, bool)
    k = np.arange(1, n - 1)
    blip[k] = (
        ~first[k]
        & ~last[k]
        & (lengths[k] == 1)
        & (np.abs(values[k + 1] / values[k - 1] - 1) < BLIP_RETURN)
        & (np.abs(values[k] / values[k - 1] - 1) >= MIN_STEP)
    )
    recorded, event, merged = np.empty(n), np.zeros(n, bool), np.zeros(n, bool)
    current = np.nan
    for k in range(n):
        if first[k]:
            current, event[k] = values[k], True
        elif not blip[k]:
            if abs(values[k] / current - 1) >= MIN_STEP:
                current, event[k] = values[k], True
            else:
                merged[k] = True
        recorded[k] = current
    return Ratio(
        row_ratio=np.repeat(recorded, lengths),
        event_rows=starts[event],
        run_starts=starts,
        run_values=values,
        blip_runs=np.flatnonzero(blip),
        merged_runs=np.flatnonzero(merged),
    )


def preclose(code_idx: np.ndarray, close: np.ndarray, row_ratio: np.ndarray) -> np.ndarray:
    """close[t-1] * ratio[t-1] / ratio[t] rounded to 0.01 元, where t-1 is the code's previous row; NaN on first rows."""
    out = np.full(close.size, np.nan)
    prev = np.flatnonzero(code_idx[1:] == code_idx[:-1])
    out[prev + 1] = np.round(close[prev] * row_ratio[prev] / row_ratio[prev + 1], 2)
    return out
