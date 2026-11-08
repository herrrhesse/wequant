"""The DataSource protocol: what every market-data source provides, in wequant's columns and units.

Columns follow wequant.data.schema: prices int32 厘, volume int64 shares, amount float64 yuan.
securities_on and corporate_actions have no table yet; their columns are defined here.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import polars as pl

SECURITIES_ON = {"code": pl.String, "date": pl.Date, "name": pl.String, "tradestatus": pl.Int8}

CORPORATE_ACTIONS = {
    "code": pl.String,
    "ex_date": pl.Date,
    "record_date": pl.Date,
    "pay_date": pl.Date,
    "cash_before_tax": pl.Float64,  # yuan per share
    "bonus_shares": pl.Float64,  # 送股 per share
    "transfer_shares": pl.Float64,  # 转增 per share
    "plan": pl.String,  # the source's own wording of the plan
}


class DataSource(Protocol):
    def securities(self) -> pl.DataFrame:
        """Every stock the source knows, listed or delisted: securities columns.

        first_date / last_date are the official listing / delisting dates; last_date is null while listed.
        """

    def calendar(self, start: dt.date | str, end: dt.date | str) -> pl.DataFrame:
        """Trading days in [start, end]: calendar columns."""

    def securities_on(self, day: dt.date | str) -> pl.DataFrame:
        """Every code the source lists on one day, with that day's name and trade status: SECURITIES_ON."""

    def daily_bars(self, code: str, start: dt.date | str, end: dt.date | str) -> pl.DataFrame:
        """Unadjusted daily bars with the exchange's preclose, trade status and ST flag: bars columns.

        Suspended days are included when the source reports them (tradestatus 0).
        """

    def adj_factors(self, code: str) -> pl.DataFrame:
        """Back-adjustment factor at each ex-date: adj_factor columns."""

    def corporate_actions(self, code: str, start_year: int, end_year: int) -> pl.DataFrame:
        """Cash dividends, bonus and transfer shares by ex-date: CORPORATE_ACTIONS."""
