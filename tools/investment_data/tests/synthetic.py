"""A small Qlib release built from known raw data and normalized like upstream.

Raw: prices in whole fen, volume in 手, amount in 千元, adjclose rounded to 0.01 元.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

N_DAYS = 40
TAG = "2024-03-01"
CALENDAR = np.array(
    [d for d in np.arange("2024-01-01", "2024-04-30", dtype="datetime64[D]") if np.is_busday(d)][: N_DAYS + 5]
)
DAYS, FUTURE = CALENDAR[:N_DAYS], CALENDAR


@dataclass
class Stock:
    code: str  # Qlib form
    start: int
    end: int  # inclusive calendar index
    base_fen: int
    ratio: dict[int, float]  # calendar index -> ratio from that day on
    suspended: tuple[int, ...] = ()
    blip: int | None = None  # day whose adjclose equals the raw close
    index: bool = False
    raw: dict[str, np.ndarray] = field(default_factory=dict)


def _ratio_path(stock: Stock) -> np.ndarray:
    days = np.arange(stock.start, stock.end + 1)
    out = np.empty(days.size)
    current = np.nan
    for i, d in enumerate(days):
        current = stock.ratio.get(int(d), current)
        out[i] = current
    return out


def make_raw(stock: Stock, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = stock.end - stock.start + 1
    if stock.index:
        close = np.round(stock.base_fen / 100 + np.cumsum(rng.normal(0, 20, n)), 4)
        open_ = np.round(close + rng.normal(0, 5, n), 4)
        high = np.round(np.maximum(open_, close) + rng.uniform(0, 5, n), 4)
        low = np.round(np.minimum(open_, close) - rng.uniform(0, 5, n), 4)
        lots = np.round(rng.uniform(1e7, 2e7, n), 2)
        amount_k = np.round(lots * rng.uniform(15, 30, n) / 10, 2)  # average share price, not points
        adjclose = np.round(close, 2)
    else:
        close_f = np.maximum(stock.base_fen + np.cumsum(rng.integers(-30, 31, n)), 200)
        open_f = close_f + rng.integers(-15, 16, n)
        high_f = np.maximum(open_f, close_f) + rng.integers(0, 15, n)
        low_f = np.minimum(open_f, close_f) - rng.integers(0, 15, n)
        close, open_, high, low = (x / 100 for x in (close_f, open_f, high_f, low_f))
        lots = rng.integers(10_000, 5_000_000, n) / 100
        vwap = rng.uniform(low, high)
        amount_k = np.round(vwap * lots * 100 / 1000, 2)
        adjclose = np.round(close * _ratio_path(stock), 2)
        if stock.blip is not None:
            adjclose[stock.blip - stock.start] = close[stock.blip - stock.start]
    return {"open": open_, "high": high, "low": low, "close": close, "volume": lots, "amount": amount_k, "adjclose": adjclose}


def normalize(raw: dict[str, np.ndarray], suspended_pos: list[int]) -> dict[str, np.ndarray]:
    raw = {k: v.astype(np.float64).copy() for k, v in raw.items()}
    for k in raw:
        raw[k][suspended_pos] = np.nan
    ratio = raw["adjclose"] / raw["close"]
    ratio = _ffill(ratio)
    out = {f: raw[f] * ratio for f in ("open", "high", "low", "close")}
    out["vwap"] = raw["amount"] / raw["volume"] * 10 * ratio
    out["volume"] = raw["volume"] / ratio
    first = out["close"][np.flatnonzero(np.isfinite(out["close"]))[0]]
    for f in ("open", "high", "low", "close", "vwap"):
        out[f] = out[f] / first
    out["volume"] = out["volume"] * first
    out["factor"] = ratio / first
    out["amount"] = raw["amount"]
    out["adjclose"] = raw["adjclose"]
    filled = _ffill(raw["close"])
    out["change"] = filled / np.r_[np.nan, filled[:-1]] - 1
    out["change"][suspended_pos] = np.nan
    return out


def _ffill(x: np.ndarray) -> np.ndarray:
    idx = np.where(np.isfinite(x), np.arange(x.size), 0)
    np.maximum.accumulate(idx, out=idx)
    return x[idx]


STOCKS = [
    Stock("BJ430047", 0, 29, 1500, {0: 1.0}),
    Stock("BJ920047", 31, N_DAYS - 1, 1520, {31: 1.0}),
    Stock("SH000300", 0, N_DAYS - 1, 350000, {0: 1.0}, index=True),
    Stock("SH600000", 0, N_DAYS - 1, 1000, {0: 1.2, 20: 1.2 * 1.02}, suspended=(10,)),
    Stock("SZ000001", 5, N_DAYS - 1, 1100, {5: 1.3}, blip=25),
    Stock("SZ300750", 0, N_DAYS - 1, 4000, {0: 50.0, 15: 50.0 * (1 + 5e-5)}),
]
CSI = {
    "csi300": [("SH600000", 0, 19), ("SH600000", 20, N_DAYS - 1), ("SZ000001", 5, N_DAYS - 1)],
    "csi500": [("SZ300750", 0, N_DAYS - 1)],
    "csi800": [("SH600000", 0, N_DAYS - 1), ("SZ300750", 0, N_DAYS - 1)],
    "csi1000": [("BJ920047", 31, N_DAYS - 1)],
    "csiall": [("SH600000", 0, N_DAYS - 1)],
}


def write_qlib(root: Path) -> list[Stock]:
    """Write calendars/, instruments/ and features/ under root; return stocks with raw data."""
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "calendars" / "day.txt").write_text("".join(f"{d}\n" for d in DAYS))
    (root / "calendars" / "day_future.txt").write_text("".join(f"{d}\n" for d in FUTURE))
    (root / "instruments" / "all.txt").write_text(
        "".join(f"{s.code}\t{DAYS[s.start]}\t{DAYS[s.end]}\n" for s in STOCKS)
    )
    for name, spans in CSI.items():
        (root / "instruments" / f"{name}.txt").write_text(
            "".join(f"{c}\t{DAYS[a]}\t{DAYS[b]}\n" for c, a, b in sorted(spans))
        )
    for i, stock in enumerate(STOCKS):
        stock.raw = make_raw(stock, seed=i)
        values = normalize(stock.raw, [d - stock.start for d in stock.suspended])
        directory = root / "features" / stock.code.lower()
        directory.mkdir(parents=True)
        for f, arr in values.items():
            np.r_[np.float32(stock.start), arr.astype(np.float32)].astype("<f4").tofile(directory / f"{f}.day.bin")
    return STOCKS


def write_release(raw_dir: Path, tmp: Path, tag: str = TAG) -> list[Stock]:
    """Write qlib_bin.tar.gz and a matching qlib_bin.manifest.json into raw_dir."""
    stocks = write_qlib(tmp / "qlib_bin")
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive = raw_dir / "qlib_bin.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tmp / "qlib_bin", arcname="qlib_bin")
    data = archive.read_bytes()
    manifest = {
        "release_tag": tag,
        "target_trade_date": str(DAYS[-1]),
        "future_start_date": str(FUTURE[N_DAYS]),
        "future_end_date": str(FUTURE[-1]),
        "dolt_commit": "a" * 32,
        "investment_data_commit": "b" * 40,
        "qlib_commit": "c" * 40,
        "image_digest": "sha256:" + "d" * 64,
        "archive_size_bytes": len(data),
        "archive_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    (raw_dir / "qlib_bin.manifest.json").write_text(json.dumps(manifest))
    return stocks


def add_member(archive: Path, name: str, payload: bytes = b"x") -> None:
    """Rewrite archive with one extra member (used to build unsafe archives)."""
    with tarfile.open(archive, "r:gz") as src:
        members = [(m, src.extractfile(m).read() if m.isfile() else None) for m in src.getmembers()]
    with tarfile.open(archive, "w:gz") as dst:
        for member, data in members:
            dst.addfile(member, io.BytesIO(data) if data is not None else None)
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        dst.addfile(info, io.BytesIO(payload))
