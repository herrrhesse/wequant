"""data_issues rows for everything the import drops or bypasses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.data.schema import TABLES

if TYPE_CHECKING:
    from tools.investment_data.adjustment import Ratio
    from tools.investment_data.convert import Recovered, Stocks


def build(
    rec: Recovered, stocks: Stocks, ratio: Ratio, dropped: np.ndarray, invalid_members: list[tuple], source: str
) -> pl.DataFrame:
    rows = [*_dropped_bars(rec, dropped), *_adj_blips(rec, stocks, ratio), *_invalid_members(invalid_members)]
    columns = [c for c in TABLES["data_issues"].schema if c != "source"]
    df = pl.DataFrame(rows, schema={c: TABLES["data_issues"].schema[c] for c in columns}, orient="row")
    return df.with_columns(source=pl.lit(source))


def _dropped_bars(rec: Recovered, dropped: np.ndarray) -> list[tuple]:
    """One row per dropped bar; a stock with every bar dropped is recorded as removed."""
    ci = rec.code_idx
    first_row = np.r_[True, ci[1:] != ci[:-1]]
    day = np.searchsorted(rec.calendar, rec.dates)
    days_since_first = day - day[first_row][np.cumsum(first_row) - 1]
    removed = np.bincount(ci[dropped], minlength=len(rec.codes)) == np.bincount(ci, minlength=len(rec.codes))
    rows = []
    for k in np.flatnonzero(dropped).tolist():
        close, volume, amount = rec.price["close"][k], rec.lots[k] * 100, rec.amount_k[k] * 1000
        detail = {
            "scale": round(float(amount / volume / close), 4),
            "days_since_first": int(days_since_first[k]),
            "close": round(float(close), 2),
            "volume": round(float(volume)),
            "amount": round(float(amount), 2),
        }
        kind, action = ("removed_security", "security_removed") if removed[ci[k]] else ("off_scale", "row_dropped")
        rows.append(("bars", rec.codes[ci[k]], rec.dates[k].item(), kind, action, _json(detail)))
    return rows


def _adj_blips(rec: Recovered, stocks: Stocks, ratio: Ratio) -> list[tuple]:
    rows = []
    for k in ratio.blip_runs.tolist():
        r = ratio.run_starts[k]
        detail = {
            "close": float(stocks.close[r]),
            "adjclose": round(float(stocks.adjclose[r]), 2),
            "ratio_before": round(float(ratio.run_values[k - 1]), 6),
            "ratio_blip": round(float(ratio.run_values[k]), 6),
            "ratio_after": round(float(ratio.run_values[k + 1]), 6),
        }
        code = rec.codes[stocks.code_idx[r]]
        rows.append(
            (
                "adj_factor",
                code,
                stocks.dates[r].item(),
                "adj_blip",
                "excluded_from_adj_factor_and_preclose",
                _json(detail),
            )
        )
    return rows


def _invalid_members(invalid_members: list[tuple]) -> list[tuple]:
    return [
        (
            "index_members",
            code,
            np.datetime64(start, "D").item(),
            "invalid_member_code",
            "span_dropped",
            _json({"list": name, "end_date": end}),
        )
        for name, code, start, end in invalid_members
    ]


def _json(detail: dict) -> str:
    return json.dumps(detail, sort_keys=True, ensure_ascii=False)
