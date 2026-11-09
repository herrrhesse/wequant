"""The golden market of the account tests: three stocks over 20 trading days of March 2024, committed to a store."""

from tests.frames import table_frame
from wequant.data.store import Store

DAYS = [f"2024-03-{d:02d}" for d in (1, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 25, 26, 27, 28)]
A, B, C = "600000.SH", "000001.SZ", "688001.SH"
BOARDS = {A: "main", B: "main", C: "star"}

# Day d1..d20: open, high, low, close, preclose in yuan; None where the stock has no bar.
BARS = {
    A: [
        (10.00, 10.10, 9.90, 10.00, 10.00),
        (10.00, 10.20, 9.95, 10.10, 10.00),
        (10.20, 10.40, 10.10, 10.30, 10.10),
        (10.40, 10.60, 10.30, 10.50, 10.30),
        (11.55, 11.55, 11.55, 11.55, 10.50),
        (7.70, 7.90, 7.60, 7.80, 7.70),
        (7.80, 7.90, 7.70, 7.80, 7.80),
        (7.90, 8.58, 7.85, 8.58, 7.80),
        (8.60, 8.80, 8.50, 8.70, 8.58),
        (8.70, 8.90, 8.60, 8.80, 8.70),
        (8.80, 9.00, 8.70, 8.90, 8.80),
        (8.90, 9.10, 8.80, 9.00, 8.90),
        (9.00, 9.20, 8.90, 9.10, 9.00),
        (9.10, 9.30, 9.00, 9.20, 9.10),
        (9.20, 9.40, 9.10, 9.30, 9.20),
        *[(9.30, 9.40, 9.20, 9.30, 9.30)] * 5,
    ],
    B: [
        (8.00, 8.10, 7.90, 8.00, 8.00),
        (8.00, 8.20, 7.95, 8.10, 8.00),
        (8.10, 8.20, 8.00, 8.10, 8.10),
        (8.10, 8.30, 8.05, 8.20, 8.10),
        *[(8.20, 8.30, 8.10, 8.20, 8.20)] * 2,
        (7.38, 7.38, 7.38, 7.38, 8.20),
        (7.00, 7.20, 6.90, 7.10, 7.38),
        *[(7.10, 7.20, 7.00, 7.10, 7.10)] * 2,
        (7.20, 7.30, 7.10, 7.20, 7.10),
        (7.30, 7.40, 7.20, 7.30, 7.20),
        (7.30, 7.40, 7.20, 7.30, 7.30),
        (7.30, 7.40, 7.20, 7.30, None),
        *[(7.30, 7.40, 7.20, 7.30, 7.30)] * 6,
    ],
    C: [
        (50.00, 50.50, 49.50, 50.00, 50.00),
        (50.00, 50.80, 49.80, 50.50, 50.00),
        (50.60, 51.20, 50.40, 51.00, 50.50),
        *[None] * 4,
        (41.00, 41.80, 40.50, 41.50, 40.80),
        (41.60, 42.00, 41.20, 41.80, 41.50),
        (41.80, 42.20, 41.60, 42.00, 41.80),
        (42.00, 42.40, 41.80, 42.20, 42.00),
        (42.20, 42.60, 42.00, 42.40, 42.20),
        (42.40, 42.80, 42.20, 42.60, 42.40),
        (42.60, 43.00, 42.40, 42.80, 42.60),
        (42.80, 43.20, 42.60, 43.00, 42.80),
        (43.00, 43.40, 42.80, 43.20, 43.00),
        (43.20, 43.60, 42.40, 42.50, 43.20),
        (40.00, 40.50, 39.80, 40.20, 40.00),
        (40.20, 40.60, 40.00, 40.40, 40.20),
        (40.40, 40.80, 40.20, 40.60, 40.40),
    ],
}
# Day d1..d20 from which each factor applies.
FACTORS = {A: [(1, 1.0), (6, 1.5)], B: [(1, 1.0)], C: [(1, 1.0), (5, 1.25), (18, 1.328125)]}


def build_store(home) -> Store:
    """Version 1 of a store at home holding the golden calendar, securities, bars and adjustment factors."""
    securities = [
        {"code": code, "exchange": code[-2:], "board": board, "first_date": DAYS[0], "last_date": DAYS[-1]}
        for code, board in BOARDS.items()
    ]
    factors = [{"code": code, "ex_date": DAYS[d - 1], "factor": f} for code, rows in FACTORS.items() for d, f in rows]
    store = Store(home)
    store.commit(
        {"source": "test"},
        replace={
            "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
            "securities": table_frame("securities", securities, source="test"),
            "bars": table_frame("bars", _bars(), volume=1_000_000, amount=1e7, preclose_derived=False, source="test"),
            "adj_factor": table_frame("adj_factor", factors),
        },
    )
    return store


def _bars() -> list[dict]:
    fields = ("open", "high", "low", "close", "preclose")
    return [
        {"code": code, "date": DAYS[d], **{f: None if v is None else round(v * 1000) for f, v in zip(fields, bar)}}
        for code, bars in BARS.items()
        for d, bar in enumerate(bars)
        if bar is not None
    ]
