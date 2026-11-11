"""Raw BaoStock files and a seed-like store for merge tests. Prices are in 厘."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from tests.frames import table_frame
from wequant.data import codes
from wequant.data.files import atomic_write, parquet_bytes
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.data.store import Store

C1, C2 = dt.date(2023, 12, 28), dt.date(2023, 12, 29)
D1, D2, D3, D4 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4), dt.date(2024, 1, 5)
CALENDAR = (C1, C2, D1, D2, D3)
MERGED = (D1, D2, D3)
VERSION = "0.9.4"
SEED = "seed@t"
SOURCE = f"baostock@{VERSION}"
DAY_COLUMNS = ("date", "code", "open", "high", "low", "close", "preclose", "volume", "amount", "adjustflag")
DAY_COLUMNS += ("turn", "tradestatus", "isST")
LIMIT_BREACH = {"table": "bars", "code": "600000.SH", "date": D1, "kind": "limit_breach", "action": "limits_voided"}
STEADY = {C1: 10000, C2: 10100, D1: 10200, D2: 10300, D3: 10400}
CONFLICTS = [  # conflict_world's, in the resolutions file's order: (date, code, kind)
    ("600000.SH", D1, "price_mismatch"),
    ("600004.SH", D1, "price_mismatch"),
    ("600001.SH", D2, "average_price"),
    ("600002.SH", D2, "average_price"),
    ("600003.SH", D3, "average_price"),
]


def text(li: int | None) -> str:
    """厘 as BaoStock's four-decimal price text."""
    return "" if li is None else f"{li // 1000}.{li % 1000 // 10:02d}00"


def day_row(
    code: str,
    day: dt.date,
    close: int | None,
    preclose: int | None = None,
    volume: int | str | None = 1000,
    amount: str | None = None,
    tradestatus: str = "1",
    st: str = "0",
    **prices: int,
) -> dict[str, str]:
    """One raw snapshot row; open, high and low default to close, preclose to close and amount to volume × close."""
    digits, exchange = codes.split(code)
    if amount is None:
        amount = "" if volume in (None, "") else f"{int(volume) * close / 1000:.4f}"
    return {
        "date": day.isoformat(),
        "code": f"{exchange.lower()}.{digits}",
        **{p: text(prices.get(p, close)) for p in ("open", "high", "low", "close")},
        "preclose": text(close if preclose is None else preclose),
        "volume": "" if volume is None else str(volume),
        "amount": amount,
        "adjustflag": "3",
        "turn": "",
        "tradestatus": tradestatus,
        "isST": st,
    }


def suspended_row(code: str, day: dt.date) -> dict[str, str]:
    """A suspended day as BaoStock lists it: tradestatus 0, every other field empty."""
    return day_row(code, day, None, volume=None, tradestatus="0")


def seed_bar(code: str, day: dt.date, close: int, preclose: int | None, volume: int = 1000, **prices: int) -> dict:
    """A seed bars row: derived preclose, no status columns."""
    return {
        "code": code,
        "date": day,
        **{p: prices.get(p, close) for p in ("open", "high", "low", "close")},
        "volume": volume,
        "amount": volume * close / 1000,
        "preclose": preclose,
        "preclose_derived": True,
        "source": SEED,
    }


@dataclass
class World:
    """Seed tables and raw files of a few stocks, written with write()."""

    bars: list[dict] = field(default_factory=list)
    adj: list[dict] = field(default_factory=list)
    days: dict[dt.date, list[dict]] = field(default_factory=dict)
    factors: dict[str, list[tuple[dt.date, float]]] = field(default_factory=dict)

    def stock(
        self,
        code: str,
        closes: dict[dt.date, int],
        steps: dict[dt.date, float] | None = None,
        seed: tuple[dt.date, ...] | None = None,
        raw: tuple[dt.date, ...] = MERGED,
        factor: float = 1.0,
    ) -> None:
        """Seed bars on the seed days (default: every day of closes) with adj rows, raw rows on the raw days.

        Each preclose is the previous close divided by that day's step, rounded to the fen.
        """
        steps = steps or {}
        seed = tuple(closes) if seed is None else seed
        previous = dict(zip(list(closes)[1:], closes.values(), strict=False))
        for day in seed:
            pre = _preclose(previous.get(day), steps.get(day, 1.0)) if day != seed[0] else None
            self.bars.append(seed_bar(code, day, closes[day], pre))
        for day in [seed[0], *sorted(d for d in steps if d in seed)] if seed else []:
            factor *= steps.get(day, 1.0) if day != seed[0] else 1.0
            self.adj.append({"code": code, "ex_date": day, "factor": round(factor, 6)})
        for day in raw:
            pre = previous.get(day)
            self.day(day, day_row(code, day, closes[day], _preclose(pre, steps.get(day, 1.0)) if pre else None))

    def day(self, day: dt.date, *rows: dict[str, str]) -> None:
        self.days.setdefault(day, []).extend(rows)

    def write(self, home: Path) -> tuple[Store, BaoStockRaw]:
        """The seed store (one version) and the raw files under home."""
        store = Store(home)
        store.commit(
            {"source": "seed", "release_tag": "t", "last_trade_date": max(CALENDAR).isoformat()},
            replace=seed_tables(self.bars, self.adj),
        )
        raw = BaoStockRaw.at(home)
        for day, rows in self.days.items():
            write_day(raw, day, rows)
        for code, rows in self.factors.items():
            write_factors(raw, code, rows)
        return store, raw


def standard_world() -> World:
    """The stocks the end-to-end tests look at; each one stands for one case."""
    w = World()
    w.stock("600000.SH", STEADY)
    for day in MERGED:
        raw_row(w, "600000.SH", day, turn="0.512300")
    w.stock("920001.BJ", STEADY, raw=(), factor=1.5)
    w.stock("600849.SH", {D1: 5000, D2: 5100, D3: 5200}, seed=())
    w.stock("600006.SH", {C1: 3700, C2: 3750, D1: 3800, D2: 3720, D3: 3730}, steps={D2: 3.8 / 3.7}, factor=3.7)
    w.stock("600008.SH", {D1: 7000, D2: 7100, D3: 7200}, raw=(), factor=2.0)
    w.stock("600009.SH", {D1: 6000, D2: 6100, D3: 6200}, seed=(D2, D3), factor=2.0)
    w.stock("600010.SH", {C1: 10000, C2: 10000, D1: 10000, D2: 8100, D3: 8100}, steps={D3: 1.25}, raw=(D1,))
    w.day(D2, day_row("600010.SH", D2, 8100, preclose=8000))
    w.day(D3, day_row("600010.SH", D3, 8100, preclose=8100))
    w.stock("600011.SH", {C1: 10000, C2: 10000, D1: 10000, D2: 8000, D3: 8000}, steps={D2: 1.1}, raw=(D1, D3))
    w.day(D2, day_row("600011.SH", D2, 8000, preclose=8000))
    duplicates(w)
    conflicts(w)
    return w


def duplicates(w: World) -> None:
    """300114.SZ and 302132.SZ: BaoStock repeats 300114's bars under 302132 before 302132 trades, on D2 with the
    amount a fen off."""
    w.stock("300114.SZ", {C1: 2000, C2: 2010, D1: 2020, D2: 2030}, raw=(D1, D2))
    for day, amount in ((D1, "2020.0000"), (D2, "2029.9900")):
        original = next(r for r in w.days[day] if r["code"] == "sz.300114")
        w.day(day, original | {"code": "sz.302132", "amount": amount})
    w.day(D3, suspended_row("300114.SZ", D3))
    w.stock("302132.SZ", {D3: 3000}, raw=())
    w.day(D3, day_row("302132.SZ", D3, 3000))


def conflicts(w: World) -> None:
    """600647.SH trades on D2 while listed as suspended; 600766.SH is listed as trading on D3 without volume."""
    w.stock("600647.SH", {C1: 10000, C2: 10000, D1: 10000, D2: 11000, D3: 11000}, raw=(D1, D3))
    w.day(D2, day_row("600647.SH", D2, 11000, preclose=10000, tradestatus="0"))
    w.stock("600766.SH", {C1: 5000, C2: 5000, D1: 5100, D2: 5200}, raw=(D1, D2))
    w.day(D3, day_row("600766.SH", D3, 5200, volume="0", amount="0.0000"))


def conflict_world() -> World:
    """CONFLICTS, one stock per resolution rule; every other bar agrees with the seed."""
    w = World()
    for code in ("600000.SH", "600001.SH", "600002.SH", "600004.SH"):
        w.stock(code, STEADY)
    w.stock("600003.SH", {D1: 5000, D2: 5100, D3: 5200}, seed=())
    # BaoStock's D1 close is 10.21; D2's exchange preclose repeats the seed's 10.20, or for 600004.SH BaoStock's
    for code in ("600000.SH", "600004.SH"):
        raw_row(w, code, D1, close="10.2100", high="10.2100", amount="10200.0000")
    raw_row(w, "600004.SH", D2, preclose="10.2100")
    raw_row(w, "600001.SH", D2, amount="0.0000")  # the seed bar is the same and its amount 10300 passes
    raw_row(w, "600002.SH", D2, volume="1100")  # 10300 yuan for 1100 shares is 9.36 a share, below low 10.30
    raw_row(w, "600003.SH", D3, amount="0.0000")  # no seed bar
    return w


def empty_resolutions(directory: Path) -> Path:
    """A resolutions file without entries, so that a test never reads the user's packaged one."""
    path = directory / "resolutions.toml"
    path.write_text("# no decisions\n", "utf-8")
    return path


def raw_row(w: World, code: str, day: dt.date, **text: str) -> None:
    """Change the text of code's raw row on day."""
    digits, exchange = codes.split(code)
    next(r for r in w.days[day] if r["code"] == f"{exchange.lower()}.{digits}").update(text)


def seed_tables(bars: list[dict], adj: list[dict]) -> dict[str, pl.DataFrame]:
    """Seed store tables from bars and adj rows: securities spans, a calendar, index tables and two issues."""
    frame = table_frame("bars", bars)
    spans = frame.group_by("code").agg(first_date=pl.col("date").min(), last_date=pl.col("date").max())
    securities = [
        {**row, "exchange": codes.split(row["code"])[1], "board": codes.board(row["code"]), "source": SEED}
        for row in spans.to_dicts()
    ]
    issues = [
        LIMIT_BREACH | {"detail": None, "source": "check_limits@t"},
        {"table": "bars", "code": "600000.SH", "date": C1, "kind": "off_scale", "action": "row_dropped"}
        | {"detail": '{"close": 1.0}', "source": SEED},
    ]
    return {
        "securities": table_frame("securities", securities),
        "calendar": table_frame("calendar", [{"date": d} for d in CALENDAR]),
        "bars": frame,
        "adj_factor": table_frame("adj_factor", adj),
        "index_bars": table_frame(
            "index_bars",
            [{"code": "000300.SH", "date": C1}],
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            amount=1.0,
            source=SEED,
        ),
        "index_members": table_frame(
            "index_members", [{"index_code": "000300.SH", "code": "600000.SH", "start_date": C1, "end_date": D3}]
        ),
        "data_issues": table_frame("data_issues", issues),
    }


def write_day(raw: BaoStockRaw, day: dt.date, rows: list[dict[str, str]]) -> None:
    frame = pl.DataFrame(rows, schema={c: pl.String for c in DAY_COLUMNS})
    raw.write(raw.daily(day), raw_frame(frame, f"query_daily_history_k_AStock(date={day})", VERSION))


def write_factors(raw: BaoStockRaw, code: str, rows: list[tuple[dt.date, float]]) -> None:
    digits, exchange = codes.split(code)
    frame = pl.DataFrame(
        {
            "code": [f"{exchange.lower()}.{digits}"] * len(rows),
            "dividOperateDate": [d.isoformat() for d, _ in rows],
            "foreAdjustFactor": [""] * len(rows),
            "backAdjustFactor": [f"{f:.6f}" for _, f in rows],
            "adjustFactor": [f"{f:.6f}" for _, f in rows],
        },
        schema={
            c: pl.String for c in ("code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor")
        },
    )
    raw.write(raw.adjust_factor(code), raw_frame(frame, f"query_adjust_factor(code={code})", VERSION))


def stale_bars(store: Store) -> None:
    """A next version whose bars files have an older layout: no turn, tradestatus or is_st, an extra vwap column."""
    manifest = store.manifest()
    version = manifest["version"] + 1
    entries = []
    for entry in manifest["tables"]["bars"]["files"]:
        frame = pl.read_parquet(store.home / entry["path"]).drop("turn", "tradestatus", "is_st")
        data = parquet_bytes(frame.with_columns(vwap=pl.lit(None, dtype=pl.Float64)))
        path = f"bars/{entry['part']}.v{version}.parquet"
        atomic_write(store.home / path, data)
        entries.append({"part": entry["part"], "path": path, "rows": frame.height, "sha256": _sha256(data)})
    manifest = {**manifest, "version": version, "tables": {**manifest["tables"], "bars": {"files": entries}}}
    data = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
    atomic_write(store.home / "manifests" / f"manifest.v{version}.json", data)
    atomic_write(store.home / "manifest.json", data)


def _preclose(previous: int | None, step: float) -> int | None:
    return None if previous is None else round(previous / step / 10) * 10


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
