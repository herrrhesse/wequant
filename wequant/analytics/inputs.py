"""The frames a run hands to the metrics, validated and turned into day series."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from wequant.analytics.series import simple_returns

RUN_DAILY = {"date": pl.Date, "equity": pl.Float64}
TRADES = {"bought": pl.Float64, "sold": pl.Float64}
BENCHMARK = {"benchmark": pl.Float64}
POSITIONS = {"date": pl.Date, "code": pl.String, "shares": pl.Float64}
ORDERS = {"date": pl.Date, "code": pl.String, "status": pl.String}
ORDER_STATUSES = ("filled", "partial", "rejected")
EXECUTED = ("filled", "partial")


@dataclass(frozen=True)
class RunSeries:
    """Day series of a run: dates and nav from the base day, the rest per run day."""

    dates: np.ndarray  # (n + 1,) datetime64[D]
    nav: np.ndarray  # (n + 1,) equity / initial capital
    returns: np.ndarray  # (n,)
    benchmark_returns: np.ndarray | None  # (n,), NaN where either day lacks a benchmark close
    turnover: np.ndarray | None  # (n,) (bought + sold) / (2 * previous equity)

    @property
    def n(self) -> int:
        return self.returns.size


def check_columns(df: pl.DataFrame, columns: dict[str, pl.DataType], name: str) -> None:
    """ValueError unless df has each of columns with its type; other columns may follow."""
    for column, dtype in columns.items():
        if column not in df.columns:
            raise ValueError(f"{name} has no column {column!r}")
        if df.schema[column] != dtype:
            raise ValueError(f"{name}.{column} is {df.schema[column]}, not {dtype}")


def run_series(daily: pl.DataFrame, calendar: pl.Series) -> RunSeries:
    """The day series of a validated daily frame whose dates are a slice of calendar."""
    check_columns(daily, RUN_DAILY, "daily")
    if daily.height < 2:
        raise ValueError("daily needs the base day and at least one run day")
    equity = daily["equity"].to_numpy()
    if not np.all(np.isfinite(equity) & (equity > 0)):
        raise ValueError("daily.equity must be finite and positive on every day")
    dates = _calendar_slice(daily["date"], calendar)
    return RunSeries(dates, equity / equity[0], simple_returns(equity), _benchmark_returns(daily), _turnover(daily))


def held_days(positions: pl.DataFrame, dates: np.ndarray) -> pl.DataFrame:
    """code and run-day number i (1..n) of every held position, sorted by code and i."""
    check_columns(positions, POSITIONS, "positions")
    positions = positions.select(list(POSITIONS))
    if positions["code"].null_count() or not np.all(np.isfinite(positions["shares"].to_numpy())):
        raise ValueError("positions need a code and finite shares on every row")
    if positions.select(pl.struct("code", "date").is_duplicated().any()).item():
        raise ValueError("positions repeat a (code, date)")
    days = pl.DataFrame({"date": dates[1:], "i": np.arange(1, dates.size)})
    rows = positions.join(days, on="date", how="left")
    if rows["i"].null_count():
        raise ValueError("positions have a date that is not a run day")
    return rows.filter(pl.col("shares") > 0).select("code", "i").sort("code", "i")


def check_orders(orders: pl.DataFrame) -> pl.Series:
    """The status of every order; ValueError for a missing column or a status outside ORDER_STATUSES."""
    check_columns(orders, ORDERS, "orders")
    status = orders["status"]
    unknown = status.filter(~status.is_in(ORDER_STATUSES).fill_null(False))
    if unknown.len():
        raise ValueError(f"orders have unknown statuses {unknown.unique().to_list()}")
    return status


def _calendar_slice(dates: pl.Series, calendar: pl.Series) -> np.ndarray:
    days = dates.to_numpy()
    if not np.all(days[1:] > days[:-1]):  # NaT from a null date compares False
        raise ValueError("daily.date must be strictly increasing")
    if calendar.dtype != pl.Date:
        raise ValueError(f"calendar is {calendar.dtype}, not Date")
    if not dates.equals(calendar.filter(calendar.is_between(dates[0], dates[-1])).sort()):
        raise ValueError("daily.date must be exactly the calendar's days from its first to its last date")
    return days


def _benchmark_returns(daily: pl.DataFrame) -> np.ndarray | None:
    if "benchmark" not in daily.columns:
        return None
    check_columns(daily, BENCHMARK, "daily")
    if daily["benchmark"].is_nan().any():
        raise ValueError("daily.benchmark has NaN; a day without a benchmark close is null")
    levels = daily["benchmark"].to_numpy()
    present = levels[~np.isnan(levels)]
    if not np.all(np.isfinite(present) & (present > 0)):
        raise ValueError("daily.benchmark must be finite and positive where present")
    return simple_returns(levels)


def _turnover(daily: pl.DataFrame) -> np.ndarray | None:
    given = TRADES.keys() & set(daily.columns)
    if not given:
        return None
    if given != TRADES.keys():
        raise ValueError("daily needs both bought and sold, or neither")
    check_columns(daily, TRADES, "daily")
    bought, sold = (daily[column].to_numpy()[1:] for column in TRADES)
    if not np.all(np.isfinite(bought) & np.isfinite(sold) & (bought >= 0) & (sold >= 0)):
        raise ValueError("daily.bought and daily.sold must be finite and non-negative on run days")
    return (bought + sold) / (2 * daily["equity"].to_numpy()[:-1])
