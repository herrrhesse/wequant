"""A synthetic data root for trying WeQuant and for end-to-end tests: 60 made-up stocks over three years.

python -m tools.demo_home --out DIR [--seed 7]

Every number comes from the seed, none from a real market; the same seed writes the same bytes. The market has every
board, ST stocks, a suspension, a delisting, a new listing, limit-up and limit-down streaks, a bonus issue and a
dividend, an index (000300.SH, synthetic members) and a few data issues, one of them error level. The store's price
limits are checked and two strategies are saved.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path

import numpy as np
import polars as pl

from wequant import service
from wequant.data.codes import board
from wequant.data.schema import TABLES
from wequant.data.store import Store
from wequant.expr.vocab import STORED_AS

SEED = 7
STAMP = "2026-01-01T00:00:00+00:00"  # the time the demo home says it was written
ALL_DAYS = pl.date_range(dt.date(2023, 1, 2), dt.date(2025, 12, 31), eager=True)
DAYS = ALL_DAYS.filter(ALL_DAYS.dt.weekday() <= 5)  # weekdays: the synthetic trading calendar
CODES = (
    [f"6{n:05d}.SH" for n in range(10, 190, 10)]  # SH main board
    + [f"00{n:04d}.SZ" for n in range(11, 181, 10)]  # SZ main board
    + ["001011.SZ", "001021.SZ", "001031.SZ"]
    + [f"30{n:04d}.SZ" for n in range(12, 132, 10)]  # ChiNext
    + [f"688{n:03d}.SH" for n in range(13, 113, 10)]  # STAR
)
HOT, SLUMP = "600030.SH", "000041.SZ"  # a locked limit-up streak; a locked limit-down streak
LOCKED = {HOT: {*range(120, 127), *range(380, 386)}, SLUMP: set(range(200, 205))}
ST = {"600060.SH": range(150, 450), "000031.SZ": range(0, 10_000)}  # trading-day indexes under ST
SUSPENDED = ("000061.SZ", range(300, 312))
DELISTED = ("600070.SH", 520)  # last bar; it rises hard before, so momentum holds it
LISTED = ("300042.SZ", 330)  # first bar; ChiNext lists without a limit for five days
EVENTS = {"000071.SZ": (250, "bonus", 2.0), "600080.SH": (400, "dividend", 300)}  # 10-for-10; 0.30 yuan
INDEX, SWAP = "000300.SH", 400  # a member leaves and another joins on SWAP
DROPPED = ("000081.SZ", 260)  # a bar recorded as dropped (off_scale): the day reads as missing
STRATEGIES = {
    "demo-momentum": (
        "演示：20 日动量前 10",
        {"factors": [{"name": "mom20", "expr": ["DIV", "adj_close", ["REF", "adj_close", 20]]}], "top_n": 10},
    ),  # fmt: skip
    "demo-csi300": (
        "演示：000300 成分里低波动前 5",
        {
            "pool": {"index": INDEX, "exclude_st": True},
            "factors": [{"name": "lowvol", "expr": ["SUB", 0.0, ["STD", ["DIV", "close", ["REF", "close", 1]], 20]]}],
            "top_n": 5,
        },
    ),  # fmt: skip
}


def build(out: Path, seed: int = SEED) -> dict:
    """Write the demo data root at out (which must not exist yet); counts of what it holds."""
    if out.exists():
        raise FileExistsError(f"{out} exists; the demo home goes into a new directory")
    rng = np.random.default_rng(seed)
    days = DAYS.to_list()
    bars, factors = {}, []
    for code in CODES:
        bars[code], events = _bars(rng, code, days)
        factors += events
    tables = _tables(days, bars, factors)
    meta = {"source": "demo", "seed": seed, "last_trade_date": days[-1].isoformat()}
    Store(out, clock=_stamp).commit(meta, replace=tables, reason="demo")
    with tempfile.TemporaryDirectory() as cache:  # panel caches stay out of the demo home
        context = service.Context(out, Path(cache), clock=_stamp)
        service.data_check(service.CheckRequest(commit=True), context)
        for id, (name, form) in STRATEGIES.items():
            service.strategy_save(service.SaveRequest(strategy=form, id=id, name=name), context)
    return {name: frame.height for name, frame in tables.items()}


def _stamp() -> str:
    """Every timestamp the demo home's store and library write, so the same seed writes the same bytes."""
    return STAMP


def _bars(rng: np.random.Generator, code: str, days: list[dt.date]) -> tuple[list[dict], list[dict]]:
    """code's bars (prices in 厘) and adj_factor rows, from its first to its last trading day."""
    first = LISTED[1] if code == LISTED[0] else 0
    last = DELISTED[1] if code == DELISTED[0] else len(days) - 1
    drift, vol = rng.normal(0.0004, 0.0006), rng.uniform(0.012, 0.03)
    shares = int(rng.integers(2, 40)) * 10_000_000  # tradable shares, for turn
    pre, factor = _fen(rng.uniform(5_000, 60_000)), 1.0
    rows, factors = [], [{"code": code, "ex_date": days[first], "factor": 1.0}]
    for t in range(first, last + 1):
        if code == SUSPENDED[0] and t in SUSPENDED[1]:
            continue
        if code in EVENTS and t == EVENTS[code][0]:
            pre, factor = _ex(pre, factor, *EVENTS[code][1:])
            factors.append({"code": code, "ex_date": days[t], "factor": round(factor, 6)})
        rising = 0.012 if code == DELISTED[0] and t > last - 60 else 0.0
        bar = _bar(rng, pre, _band(code, t), drift + rising, vol, code, t - first, t)
        volume = int(rng.lognormal(10, 0.6)) * 100 + 100
        average = (bar["high"] + bar["low"] + 2 * bar["close"]) / 4
        traded = {
            "volume": volume,
            "amount": round(volume * average / 1000, 2),
            "turn": round(volume / shares * 100, 6),
        }
        rows.append({"code": code, "date": days[t], **bar, "preclose": pre, **traded, "is_st": _st(code, t)})
        pre = bar["close"]
    return rows, factors


def _bar(rng, pre: int, bp: int | None, drift: float, vol: float, code: str, day: int, t: int) -> dict:
    """One day's open, high, low and close in 厘 within the limits of bp around pre (none on a free listing day)."""
    up, down = (_limit(pre, bp, 1), _limit(pre, bp, -1)) if bp else (pre * 3, 10)
    if t in LOCKED.get(code, ()):
        price = up if code == HOT else down
        return dict.fromkeys(("open", "high", "low", "close"), price)
    move = 0.4 if bp is None and day == 0 else drift + vol * rng.standard_normal()
    close = _clip(_fen(pre * (1 + move)), down, up)
    open_ = _clip(_fen(pre * (1 + 0.3 * move + 0.004 * rng.standard_normal())), down, up)
    high = _clip(_fen(max(open_, close) * (1 + abs(0.008 * rng.standard_normal()))), down, up)
    low = _clip(_fen(min(open_, close) * (1 - abs(0.008 * rng.standard_normal()))), down, up)
    return {"open": open_, "high": max(high, open_, close), "low": min(low, open_, close), "close": close}


def _band(code: str, t: int) -> int | None:
    """Basis points of the day's price limit; None on a new listing's free days."""
    if code == LISTED[0] and t - LISTED[1] < 5:
        return None
    if _st(code, t):
        return 500
    return 2000 if board(code) in ("chinext", "star") else 1000


def _ex(pre: int, factor: float, kind: str, size: float) -> tuple[int, float]:
    """The exchange's ex-rights preclose and the factor after it."""
    adjusted = _fen(pre / size) if kind == "bonus" else pre - int(size)
    return adjusted, factor * pre / adjusted


def _limit(pre: int, bp: int, side: int) -> int:
    """The rule book's limit in 厘: pre moved by bp, half up to the fen, at least a fen from pre and above zero."""
    rounded = (pre * (10_000 + side * bp) + 50_000) // 100_000 * 10
    return max(rounded, pre + 10) if side > 0 else max(min(rounded, pre - 10), 10)


def _fen(li: float) -> int:
    return int(li / 10 + 0.5) * 10


def _clip(price: int, low: int, high: int) -> int:
    return min(max(price, low), high)


def _st(code: str, t: int) -> bool:
    return t in ST.get(code, ())


def _tables(days: list[dt.date], bars: dict[str, list[dict]], factors: list[dict]) -> dict[str, pl.DataFrame]:
    rows = [row for code_rows in bars.values() for row in code_rows]
    dropped = (DROPPED[0], days[DROPPED[1]])
    kept = [r for r in rows if (r["code"], r["date"]) != dropped]
    frames = {
        "calendar": pl.DataFrame({"date": days}),
        "securities": pl.DataFrame([_security(i, code, bars[code]) for i, code in enumerate(CODES)]),
        "bars": pl.DataFrame(kept).with_columns(preclose_derived=False, tradestatus=1, source=pl.lit("demo")),
        "daily_status": _status(days, bars),
        "adj_factor": pl.DataFrame(factors),
        "index_bars": _index_bars(days, bars),
        "index_members": _members(days),
        "data_issues": _issues(days, bars),
    }
    return {name: _typed(name, frame) for name, frame in frames.items()}


def _security(i: int, code: str, rows: list[dict]) -> dict:
    exchange = code[-2:]
    name = f"演示{i + 1:02d}"
    span = {"first_date": rows[0]["date"], "last_date": rows[-1]["date"]}
    return {"code": code, "exchange": exchange, "board": board(code), "name": name, **span, "source": "demo"}


def _status(days: list[dt.date], bars: dict[str, list[dict]]) -> pl.DataFrame:
    """A row for every listed day: suspended days have no bar (tradestatus 0); the dropped bar's day says traded."""
    rows = []
    for code, code_rows in bars.items():
        traded = {r["date"] for r in code_rows}
        first, last = days.index(code_rows[0]["date"]), days.index(code_rows[-1]["date"])
        for t in range(first, last + 1):
            rows.append({"code": code, "date": days[t], "tradestatus": int(days[t] in traded), "is_st": _st(code, t)})
    return pl.DataFrame(rows).with_columns(source=pl.lit("demo"))


def _index_members(days: list[dt.date]) -> list[str]:
    return [c for c in CODES if board(c) in ("main", "star") and c not in (DELISTED[0], LISTED[0])][:31]


def _members(days: list[dt.date]) -> pl.DataFrame:
    """30 members from the first day; on SWAP the 30th leaves and the 31st joins."""
    codes = _index_members(days)
    spans = [(c, days[0], days[-1]) for c in codes[:29]]
    spans += [(codes[29], days[0], days[SWAP - 1]), (codes[30], days[SWAP], days[-1])]
    return pl.DataFrame(spans, schema=["code", "start_date", "end_date"], orient="row").with_columns(
        index_code=pl.lit(STORED_AS.get(INDEX, INDEX))
    )


def _index_bars(days: list[dt.date], bars: dict[str, list[dict]]) -> pl.DataFrame:
    """An equal-weight level of the members' closes, 3,000 on the first day."""
    closes = pl.DataFrame([r for c in _index_members(days)[:30] for r in bars[c]]).select("code", "date", "close")
    level = (
        closes.with_columns(rel=pl.col("close") / pl.col("close").first().over("code"))
        .group_by("date")
        .agg(pl.col("rel").mean())
    )
    level = level.sort("date").select("date", price=(pl.col("rel") * 3_000_000).round().cast(pl.Int32))
    prices = {name: pl.col("price") for name in ("open", "high", "low", "close")}
    return level.select(
        "date", **prices, code=pl.lit(INDEX), volume=pl.lit(1), amount=pl.lit(1.0), source=pl.lit("demo")
    )


def _issues(days: list[dt.date], bars: dict[str, list[dict]]) -> pl.DataFrame:
    """A dropped bar (gap), an error-level corrected price, a suspicious volume and a notice-level added bar."""
    hot = next(r for r in bars[HOT] if r["date"] == days[140])
    detail = {
        "fields": ["close"],
        "before": {"close": hot["close"] + 10},
        "baostock": {"close": hot["close"] + 10},
        "after": {"close": hot["close"]},
        "category": "demo",
        "level": "error",
        "verifications": [],
        "evidence": "synthetic: a corrected close, so runs show an error-level anomaly",
    }
    rows = [
        ("bars", DROPPED[0], days[DROPPED[1]], "off_scale", "row_dropped", {"close": 1.0}),
        ("bars", HOT, days[140], "price_mismatch", "resolved_corrected", detail),
        ("bars", "600090.SH", days[200], "volume_mismatch", "value_replaced", {"volume": 1200, "store_volume": 1000}),
        ("bars", LISTED[0], days[LISTED[1]], "bar_added", "row_added", {"position": "new_code"}),
    ]
    found = [
        {
            "table": t,
            "code": c,
            "date": d,
            "kind": k,
            "action": a,
            "detail": json.dumps(x, sort_keys=True),
            "source": "demo",
        }
        for t, c, d, k, a, x in rows
    ]
    return pl.DataFrame(found)


def _typed(name: str, frame: pl.DataFrame) -> pl.DataFrame:
    """frame in the table's columns and types, missing columns null, sorted by its key."""
    table = TABLES[name]
    present = frame.columns
    frame = frame.select(
        pl.col(c).cast(t) if c in present else pl.lit(None, dtype=t).alias(c) for c, t in table.schema.items()
    )
    frame = frame.sort(list(table.key)) if table.key else frame
    table.validate(frame)
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.demo_home", description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="a new directory for the demo data root")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    counts = build(args.out, args.seed)
    print(f"demo home at {args.out}: " + ", ".join(f"{n} {name}" for name, n in counts.items()))
    print(f"serve it: uv run wequant serve --home {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
