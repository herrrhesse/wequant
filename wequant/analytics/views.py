"""A run's day and period views: what a report or a chart shows, from the definitions the metrics use."""

from __future__ import annotations

import numpy as np
import polars as pl

from wequant.analytics.inputs import run_series
from wequant.analytics.series import compound, drawdowns, excess, exposure, period_keys, relative_levels

DAILY_VIEW = {
    "date": pl.Date,
    "equity": pl.Float64,
    "cash": pl.Float64,
    "bought": pl.Float64,
    "sold": pl.Float64,
    "benchmark": pl.Float64,
    "positions": pl.Int64,
    "invested": pl.Float64,
    "nav": pl.Float64,
    "benchmark_nav": pl.Float64,
    "excess": pl.Float64,
    "drawdown": pl.Float64,
    "exposure": pl.Float64,
}
PERIOD_VIEW = {
    "period": pl.String,
    "return": pl.Float64,
    "benchmark_return": pl.Float64,
    "excess": pl.Float64,
    "max_drawdown": pl.Float64,
}


def daily_view(daily: pl.DataFrame, positions: pl.DataFrame) -> pl.DataFrame:
    """DAILY_VIEW, one row per day of a run's daily frame (the base day first): its columns, the holdings at the close,
    nav, the benchmark's level relative to the base day, the excess over it, the drawdown and the exposure; null
    where the benchmark has no close."""
    held = positions.group_by("date").agg(positions=pl.len(), invested=pl.col("value").sum())
    frame = daily.join(held, on="date", how="left", maintain_order="left").with_columns(
        pl.col("positions").fill_null(0), pl.col("invested").fill_null(0.0)
    )
    if "benchmark" not in frame.columns:
        frame = frame.with_columns(benchmark=pl.lit(None, dtype=pl.Float64))
    nav = run_series(daily, daily["date"]).nav
    relative = relative_levels(frame["benchmark"].to_numpy())
    columns = {
        "nav": nav,
        "benchmark_nav": relative,
        "excess": excess(nav, relative),
        "drawdown": drawdowns(nav),
        "exposure": exposure(frame["invested"].to_numpy(), frame["equity"].to_numpy()),
    }
    frame = frame.with_columns(pl.Series(name, values).fill_nan(None) for name, values in columns.items())
    return frame.select(list(DAILY_VIEW)).cast(DAILY_VIEW)


def period_view(daily: pl.DataFrame, period: str) -> pl.DataFrame:
    """PERIOD_VIEW, one row per year or month of a run's run days, not annualised: the compound return, the
    benchmark's (null when a close is missing), the excess, and the largest drawdown from the previous period's last
    nav (the base day's for the first)."""
    series = run_series(daily, daily["date"])
    keys = period_keys(series.dates[1:], period)
    starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))
    stops = np.append(starts[1:], keys.size)
    returns = compound(series.returns, keys)
    benchmark = compound(series.benchmark_returns, keys) if series.benchmark_returns is not None else returns * np.nan
    worst = [drawdowns(series.nav[start : stop + 1]).max() for start, stop in zip(starts, stops)]
    columns = {
        "period": [str(key) for key in keys[starts]],
        "return": returns,
        "benchmark_return": benchmark,
        "excess": excess(1 + returns, 1 + benchmark),
        "max_drawdown": worst,
    }
    return pl.DataFrame(columns).with_columns(pl.col(pl.Float64).fill_nan(None)).cast(PERIOD_VIEW)
