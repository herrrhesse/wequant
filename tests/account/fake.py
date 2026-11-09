"""A stand-in for Panel with the fields an Account reads, built from small hand-written grids."""

import numpy as np


class FakePanel:
    """Grids are (T, N), or (T,) for one code; traded where close is finite; FREE limits unless up and down are set."""

    version, key, rules_key = 1, "fake", "fake-r1"

    def __init__(self, dates, codes, boards, open, close, factor=None, up=None, down=None):
        self.dates = np.array(dates, dtype="datetime64[D]")
        self.codes, self.boards = np.array(codes), np.array(boards)
        self.exchanges = np.array([code[-2:] for code in codes])
        self._raw = {"open": self._grid(open), "close": self._grid(close)}
        self.traded = np.isfinite(self._raw["close"])
        self.factor = np.ones(self.traded.shape) if factor is None else self._grid(factor)
        bound = np.where(self.traded, np.inf, np.nan)
        self.limit_up_price = bound if up is None else self._grid(up)
        self.limit_down_price = -bound if down is None else self._grid(down)
        self.limit_known = ~np.isnan(self.limit_up_price)

    def raw(self, field: str) -> np.ndarray:
        return self._raw[field]

    def row(self, day) -> int:
        """Row of a date; KeyError if it is not one of dates."""
        rows = np.flatnonzero(self.dates == np.datetime64(day, "D"))
        if not rows.size:
            raise KeyError(f"{day} is not a trading day of this panel")
        return int(rows[0])

    def _grid(self, values) -> np.ndarray:
        return np.asarray(values, dtype=np.float64).reshape(len(self.dates), len(self.codes))
