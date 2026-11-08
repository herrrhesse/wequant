"""The raw data layer: source responses kept exactly as returned, one file per completed unit.

    raw/baostock/meta/stock_basic.parquet
    raw/baostock/meta/trade_dates.parquet
    raw/baostock/meta/stock_industry.parquet
    raw/baostock/daily/<YYYY>/<YYYY-MM-DD>.parquet
    raw/baostock/adjust_factor/<code>.parquet           code as 600000.SH

Every response field is a string column, untouched. Three columns are added: _request, _fetched_at (UTC)
and _baostock_version. A file that exists is complete (wequant.data.files.atomic_write).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from wequant.data.files import atomic_write, parquet_bytes
from wequant.data.store import wequant_home

META_COLUMNS = ("_request", "_fetched_at", "_baostock_version")


def raw_frame(frame: pl.DataFrame, request: str, version: str, fetched_at: dt.datetime | None = None) -> pl.DataFrame:
    """A response table (string columns) with the metadata columns added."""
    fetched_at = fetched_at or dt.datetime.now(dt.UTC)
    if any(dtype != pl.String for dtype in frame.schema.values()):
        raise ValueError(f"raw tables hold strings only: {dict(frame.schema)}")
    return frame.with_columns(
        _request=pl.lit(request),
        _fetched_at=pl.lit(fetched_at.isoformat(timespec="seconds")),
        _baostock_version=pl.lit(version),
    )


@dataclass(frozen=True)
class BaoStockRaw:
    root: Path

    @classmethod
    def at(cls, home: Path | None = None) -> BaoStockRaw:
        return cls(Path(home or wequant_home()) / "raw" / "baostock")

    @property
    def stock_basic(self) -> Path:
        return self.root / "meta" / "stock_basic.parquet"

    @property
    def trade_dates(self) -> Path:
        return self.root / "meta" / "trade_dates.parquet"

    @property
    def stock_industry(self) -> Path:
        return self.root / "meta" / "stock_industry.parquet"

    def daily(self, day: dt.date) -> Path:
        return self.root / "daily" / f"{day.year}" / f"{day.isoformat()}.parquet"

    def adjust_factor(self, code: str) -> Path:
        return self.root / "adjust_factor" / f"{code}.parquet"

    def daily_days(self) -> set[dt.date]:
        return {dt.date.fromisoformat(p.stem) for p in (self.root / "daily").glob("*/*.parquet")}

    def adjust_factor_codes(self) -> set[str]:
        return {p.stem for p in (self.root / "adjust_factor").glob("*.parquet")}

    def write(self, path: Path, frame: pl.DataFrame) -> None:
        atomic_write(path, parquet_bytes(frame))

    def remove_temp_files(self) -> list[Path]:
        """Temporary files a killed run left behind."""
        leftovers = sorted(self.root.rglob(".*.tmp")) if self.root.exists() else []
        for path in leftovers:
            path.unlink()
        return leftovers
