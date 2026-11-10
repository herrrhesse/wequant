"""Small markets for the engine tests: days, bars in yuan, factors, an index and daily status, committed to a store."""

from __future__ import annotations

import numpy as np

from tests.frames import table_frame
from wequant.data.store import Store

A, B = "600000.SH", "000001.SZ"
WEEK = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
ROTATION = {  # the rotation market: A and B take turns at the best one-day return
    A: [(10.00, 10.00), (10.20, 10.50), (10.40, 10.20), (10.60, 10.80), (10.90, 11.00)],
    B: [(20.00, 20.00), (19.80, 19.50), (19.60, 21.00), (21.20, 21.50), (21.40, 21.00)],
}
CSI300 = ("000300.SH", [3000, 3030, 2970, 3060, 3090])
ORDER_COLUMNS = ("date", "code", "side", "requested", "shares", "price", "value", "commission", "stamp_tax")
ORDER_COLUMNS += ("transfer_fee", "slippage", "status", "reason")
MIXED = ("000001.SZ", "000002.SZ", "300001.SZ", "600000.SH", "600004.SH", "830799.BJ")
MIXED_BOARDS = {"300001.SZ": "chinext", "830799.BJ": "bse"}
GAPS = {"000002.SZ": (3, 4, 9, 15, 16, 17)}  # rows without a bar


def build_store(home, days, bars, factors=(), index=None, boards=None, status=(), amounts=None) -> Store:
    """Version 1 of a store at home.

    bars maps a code to one None | (open, close) | (open, close, preclose) per day; the preclose defaults to the previous
    bar's close (on the first bar, its own close). factors are (code, ex_date, factor) rows, default 1.0 from days[0];
    index is (code, closes); status holds daily_status rows {code, date, tradestatus, is_st}.
    """
    boards, amounts = boards or {}, amounts or {}
    securities = [
        {
            "code": code,
            "exchange": code[-2:],
            "board": boards.get(code, "main"),
            "first_date": days[0],
            "last_date": days[-1],
        }
        for code in bars
    ]
    factors = [{"code": c, "ex_date": d, "factor": f} for c, d, f in factors] or [
        {"code": code, "ex_date": days[0], "factor": 1.0} for code in bars
    ]
    tables = {
        "calendar": table_frame("calendar", [{"date": day} for day in days]),
        "securities": table_frame("securities", securities, source="test"),
        "bars": table_frame(
            "bars", _bars(days, bars, amounts), volume=1_000_000, preclose_derived=False, source="test"
        ),
        "adj_factor": table_frame("adj_factor", factors),
    }
    if index is not None:
        tables["index_bars"] = _index(days, *index)
    if status:
        tables["daily_status"] = table_frame("daily_status", list(status), source="test")
    store = Store(home)
    store.commit({"source": "test"}, replace=tables)
    return store


def _bars(days, bars, amounts) -> list[dict]:
    rows = []
    for code, seq in bars.items():
        previous = None
        for day, bar in zip(days, seq):
            if bar is not None:
                open_, close, *given = bar
                preclose = given[0] if given else close if previous is None else previous
                prices = {"open": open_, "high": max(open_, close), "low": min(open_, close), "close": close}
                prices = {name: round(v * 1000) for name, v in (prices | {"preclose": preclose}).items()}
                rows.append({"code": code, "date": day, **prices, "amount": amounts.get(code, 1e7)})
                previous = close
    return rows


def _index(days, code, closes):
    prices = [dict.fromkeys(("open", "high", "low", "close"), round(x * 1000)) for x in closes]
    rows = [{"code": code, "date": day, **p} for day, p in zip(days, prices)]
    return table_frame("index_bars", rows, volume=1, amount=1.0, source="test")


def business_days(n: int, first: str = "2024-01-02") -> list[str]:
    """n weekdays from first, as ISO text."""
    days = np.busday_offset(np.datetime64(first), np.arange(n), roll="forward")
    return [str(day) for day in days]


def mixed_market(n: int = 25, seed: int = 7) -> tuple[list[str], dict]:
    """Days and (open, close, preclose) bars of MIXED from a seeded walk inside the price limits; GAPS have no bar."""
    rng = np.random.default_rng(seed)
    bars = {}
    for code in MIXED:
        close, seq = round(10.0 + 10.0 * rng.random(), 2), []
        for t in range(n):
            open_ = round(close * rng.uniform(0.97, 1.03), 2)
            new = round(open_ * rng.uniform(0.95, 1.05), 2)
            seq.append(None if t in GAPS.get(code, ()) else (open_, new, close))
            close = close if seq[-1] is None else new
        bars[code] = seq
    return business_days(n), bars
