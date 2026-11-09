"""The batch protocol every engine trades by: a day's target weights as one call of sells, then buys and stand-ins."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np

from wequant.account.ledger import Fills

if TYPE_CHECKING:
    from wequant.account.account import Account


class Target(NamedTuple):
    """What to hold after the open, decided at the last close: weights of equity, buy priority and stand-ins."""

    weights: np.ndarray  # (N,): NaN leaves a column alone, 0 sells all of it, w > 0 aims at w × equity
    priority: np.ndarray  # (N,): buys go highest first, ties by column, NaN last
    backups: np.ndarray  # (K,) columns, best first, that stand in for new buys getting no shares


class Batch(NamedTuple):
    """One account.trade call of the protocol: its columns and quantities in submission order, and their fills."""

    cols: np.ndarray
    qty: np.ndarray
    fills: Fills


def trade_target(account: Account, target: Target, band: float | None = None) -> list[Batch]:
    """The open day's first trades toward target: sells by column, buys by priority, then stand-ins for refused buys.

    band None leaves held columns with a positive weight alone; a number trades them when |value - goal| > band × goal.
    """
    weights, priority, backups = _checked(account, target)
    price, held, equity = _prices(account), account.shares > 0, account.equity
    kept = held & (weights > 0)
    goal = weights * equity
    weights[kept if band is None else kept & (np.abs(account.values - goal) <= band * goal)] = np.nan
    gap = weights * equity - account.values
    sized = (weights > 0) & (gap != 0)
    sized[backups] = True
    if np.any(sized & ~(price > 0)):
        raise ValueError("a column to buy or resize has neither a fill price today nor a close yesterday")
    batches: list[Batch] = []
    sold = np.flatnonzero(gap < 0)
    _call(account, sold, np.where(weights[sold] == 0, -np.inf, -np.floor(-gap[sold] / price[sold])), batches)
    bought = np.flatnonzero(gap > 0)
    bought = bought[np.lexsort((bought, -priority[bought]))]
    refused = _buy(account, bought, gap[bought], price, batches)
    slots = weights[bought[refused & ~held[bought]]]
    queue = backups[(account.shares[backups] == 0) & ~(weights[backups] > 0)]  # a backup sold today may buy back in
    while slots.size and queue.size:
        k = min(slots.size, queue.size)
        refused = _buy(account, queue[:k], slots[:k] * equity, price, batches)
        slots, queue = slots[:k][refused], queue[k:]
    return batches


def _checked(account: Account, target: Target) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Own copies of a valid target; RuntimeError unless a day is open and nothing has traded on it."""
    n = len(account.shares)
    weights, priority = np.array(target.weights, dtype=np.float64), np.asarray(target.priority, dtype=np.float64)
    backups = np.asarray(target.backups)
    backups = backups.astype(np.int64) if backups.size == 0 else backups
    if weights.shape != (n,) or priority.shape != (n,):
        raise ValueError(f"weights and priority must have shape ({n},)")
    if np.any((weights < 0) | np.isinf(weights)):
        raise ValueError("a weight is negative or infinite")
    if backups.ndim != 1 or not np.issubdtype(backups.dtype, np.integer) or np.any((backups < 0) | (backups >= n)):
        raise ValueError(f"backups must be columns in [0, {n})")
    if not account.fresh:
        raise RuntimeError("trade_target must be the open day's first trade")
    return weights, priority, backups.astype(np.int64)


def _prices(account: Account) -> np.ndarray:
    """Each column's sizing price: today's fill price, else yesterday's close in today's adjustment."""
    panel, t = account.panel, account.t
    fill = panel.raw(account.execution.price)[t]
    return np.where(fill > 0, fill, panel.raw("close")[t - 1] * panel.factor[t - 1] / panel.factor[t])


def _buy(account: Account, cols: np.ndarray, budgets: np.ndarray, price: np.ndarray, batches: list) -> np.ndarray:
    """Buy cols for budgets scaled together to the cash; True where a column got no shares."""
    if not cols.size:
        return np.zeros(0, dtype=bool)
    qty = np.floor(budgets * min(1.0, account.cash / budgets.sum()) / price[cols])
    refused = qty == 0
    fills = _call(account, cols, qty, batches)
    if fills is not None:
        refused[~refused] = fills.shares == 0
    return refused


def _call(account: Account, cols: np.ndarray, qty: np.ndarray, batches: list) -> Fills | None:
    """One account.trade call of the orders with a quantity, recorded in batches; None when there is none."""
    cols, qty = cols[qty != 0], qty[qty != 0]
    if not cols.size:
        return None
    fills = account.trade(cols, qty)
    batches.append(Batch(cols, qty, fills))
    return fills
