"""The records of a run: per-batch fills and the frames wequant.analytics takes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import polars as pl

from wequant.analytics import inputs

FILLED, PARTIAL, REJECTED = 0, 1, 2  # indexes into wequant.analytics.inputs.ORDER_STATUSES
ORDER_REASONS = ("no_position", "no_bar", "limit_unknown", "limit_up", "limit_down", "t1", "lot", "cash")
DAILY = {**inputs.RUN_DAILY, "cash": pl.Float64, **inputs.TRADES}
POSITIONS = {**inputs.POSITIONS, "value": pl.Float64}
ORDERS = {
    **inputs.ORDERS,
    "side": pl.String,
    "requested": pl.Float64,
    "shares": pl.Float64,
    "price": pl.Float64,
    **inputs.ORDER_AMOUNTS,
    "commission": pl.Float64,
    "stamp_tax": pl.Float64,
    "transfer_fee": pl.Float64,
    "slippage": pl.Float64,
    "reason": pl.String,
}
_COSTS = ("commission", "stamp_tax", "transfer_fee", "slippage")


class Fills(NamedTuple):
    """What each order of one batch got, in submission order; money in yuan."""

    shares: np.ndarray  # (K,) float64 filled, >= 0; fractional only on a full exit
    price: np.ndarray  # (K,) float64 the day's raw fill price; NaN where the stock has no bar
    value: np.ndarray  # (K,) whole shares × price, plus a sold fraction × price half up to the fen
    commission: np.ndarray  # (K,)
    stamp_tax: np.ndarray  # (K,)
    transfer_fee: np.ndarray  # (K,)
    slippage: np.ndarray  # (K,)
    status: np.ndarray  # (K,) int8 FILLED, PARTIAL or REJECTED
    reason: np.ndarray  # (K,) int8 index into ORDER_REASONS; -1 when filled in full


@dataclass(frozen=True)
class Ledger:
    """A run's records as wequant.analytics inputs, plus the identities a run archive keeps."""

    daily: pl.DataFrame  # DAILY: the base day, then one row per closed day
    positions: pl.DataFrame  # POSITIONS: one row per (closed day, held code), shares > 0
    orders: pl.DataFrame  # ORDERS: one row per order; batches in call order, rows in submission order
    meta: dict  # store_version, data_key, rules_key, code_version, execution, execution_version, cash, start


def fills_from_rows(rows: list[tuple]) -> Fills:
    """Read-only Fills from one row per order: shares, price, value and the four costs in 厘, status and reason."""
    columns = list(zip(*rows)) or [()] * len(Fills._fields)
    shares, price, *money, status, reason = (np.array(column, dtype=np.float64) for column in columns)
    got = Fills(shares, price, *(m / 1000 for m in money), status.astype(np.int8), reason.astype(np.int8))
    for array in got:  # the ledger keeps these arrays
        array.flags.writeable = False
    return got


def daily_frame(days: list[tuple], dates: np.ndarray) -> pl.DataFrame:
    """DAILY from one (row, equity, cash, bought, sold) per day."""
    rows, *numbers = (np.array(column) for column in zip(*days))
    return pl.DataFrame(dict(zip(DAILY, [dates[rows], *numbers])), schema=DAILY)


def positions_frame(holdings: list[tuple], dates: np.ndarray, codes: np.ndarray) -> pl.DataFrame:
    """POSITIONS from one (row, columns, shares, values) per closed day."""
    if not holdings:
        return pl.DataFrame(schema=POSITIONS)
    rows = np.concatenate([np.full(len(cols), row) for row, cols, _, _ in holdings])
    cols, shares, values = (np.concatenate(part) for part in list(zip(*holdings))[1:])
    return pl.DataFrame(dict(zip(POSITIONS, [dates[rows], codes[cols], shares, values])), schema=POSITIONS)


def orders_frame(batches: list[tuple], dates: np.ndarray, codes: np.ndarray, amount: np.ndarray) -> pl.DataFrame:
    """ORDERS from one (row, columns, quantities, Fills) per batch, with each stock's (T, N) day amount in yuan."""
    if not batches:
        return pl.DataFrame(schema=ORDERS)
    rows = np.concatenate([np.full(len(cols), row) for row, cols, _, _ in batches])
    cols, qty = (np.concatenate(part) for part in list(zip(*batches))[1:3])
    got = Fills(*(np.concatenate(part) for part in zip(*(batch[3] for batch in batches))))
    columns = {
        "date": dates[rows],
        "code": codes[cols],
        "status": _names(got.status, inputs.ORDER_STATUSES),
        "side": np.where(qty < 0, "sell", "buy"),
        "requested": np.abs(qty),
        "shares": got.shares,
        "price": pl.Series(got.price).fill_nan(None),
        "value": got.value,
        "day_amount": pl.Series(amount[rows, cols], dtype=pl.Float64).fill_nan(None),
        **{name: getattr(got, name) for name in _COSTS},
        "reason": _names(got.reason, ORDER_REASONS),
    }
    return pl.DataFrame(columns, schema=ORDERS)


def _names(index: np.ndarray, names: tuple[str, ...]) -> pl.Series:
    """names[i] for each index i, null for -1."""
    return pl.Series(index).replace_strict(dict(enumerate(names)), default=None, return_dtype=pl.String)
