"""Result models several operations share: warnings, metrics, saved-version labels, orders, sell reasons, anomalies."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Literal

from wequant.account.ledger import ORDER_REASONS
from wequant.backtest.anomalies import CHANNELS, DECISIONS
from wequant.data.schema import CLASSES, LEVELS
from wequant.service.base import Model

Params = dict[str, int | float | str | None]
Action = Literal["buy", "keep", "sell", "wait", "none"]
Role = Literal["target", "backup", "none"]
Op = Literal["gt", "ge", "lt", "le", "eq", "between"]


class Warning(Model):
    """One warning, in the engine's order."""

    code: str
    """snake_case, e.g. stale_holdings, data_anomaly, delisting_approximate."""
    text: str
    """English, for logs: a fixed template filled from params."""
    params: Params
    """Every number and date of text (dates ISO, shares as 0..1); the frontend renders its own text from these."""


def warnings_out(found: Mapping[str, tuple[str, Params]]) -> list[Warning]:
    """Warnings of a core code -> (text, params) mapping, in its order."""
    return [Warning(code=code, text=text, params=params) for code, (text, params) in found.items()]


class MetricOut(Model):
    """One metric of wequant.analytics."""

    value: float | int | dt.date | None
    """null: no value; see reason."""
    unit: str
    """fraction, ratio, days, count or date."""
    reason: str | None
    """Why there is no value; null when there is one."""


class SavedRef(Model):
    """A saved strategy version, for display."""

    id: str
    n: int


class SellReason(Model):
    """Why a holding is sold on a decision day."""

    kind: Literal["universe", "filter", "no_score", "rank", "slots"]
    step: str | None
    """The first pool item or chain step it failed, or the factor (or formula) without a value; null: not applicable."""
    rank: int | None
    """Its rank; null: not applicable."""
    limit: int | None
    """hold_rank or top_n; null: not applicable."""


class OrderRow(Model):
    """One order of a run, as archived."""

    date: dt.date
    """The fill day."""
    code: str
    side: Literal["buy", "sell"]
    status: Literal["filled", "partial", "rejected"]
    requested: float
    """Shares asked for (the whole holding for a full exit)."""
    shares: float
    """Shares filled."""
    price: float | None
    """Yuan, raw fill price (0 for a write-down); null: no bar that day."""
    value: float
    """Yuan."""
    day_amount: float | None
    """Yuan the stock traded that day; null: unknown."""
    commission: float
    stamp_tax: float
    transfer_fee: float
    slippage: float
    reason: Literal[ORDER_REASONS] | None
    """Why rejected or partial, or delist_write_down / delist_exit; null: filled rebalance order."""


class AnomalyUse(Model):
    """One use of an anomalous cell by a run, a screen or an explain."""

    date: dt.date
    """The decision day (signal), the fill day (fill) or the valued close (valuation)."""
    code: str
    channel: Literal[CHANNELS]
    anomaly_date: dt.date
    kind: str
    """The data_issues kind."""
    anomaly_class: Literal[CLASSES]
    """The archive's class column: price, volume, bar, gap, limits, status or adjustment."""
    level: Literal[LEVELS]
    """error, suspicious or notice."""
    root: str | None
    """Signal: the first root that read the cell; null otherwise."""
    full_history: bool | None
    """Signal: that root reads a stock's whole history; null otherwise."""
    decision: Literal[DECISIONS] | None
    """Signal: the action (backtest) or role (screen, explain); null otherwise."""
    order_status: str | None
    """Fill: filled, partial or the rejection reason; null otherwise."""
    value: float | None
    """Valuation: yuan of the holding; null otherwise."""
    share: float | None
    """Valuation: of equity; null otherwise."""
    seq: int
    """Fill: the order's row in the orders frame; 0 otherwise."""


class LevelCounts(Model):
    """Anomalous cells used, by level."""

    error: int
    suspicious: int
    notice: int


class DataAnomaly(Model):
    """How much anomalous data a result used; with exclude_day only the unavoidable remainder."""

    codes: int
    cells: int
    levels: LevelCounts
    kinds: dict[str, int]
    """Cells by data_issues kind."""
    decisions: int
    """Decisions whose signal windows read an anomalous cell."""
    fills: int
    """Orders on anomalous cells."""
    max_share: float
    """Largest share of equity held on anomalous cells."""
    max_share_day: dt.date | None
    """null: no valuation use."""
    unavoidable: int
    """Uses exclusion cannot remove point-in-time (adjustment and gap classes)."""
    sample: list[AnomalyUse]
    """The first 20 uses by date (all of them for a screen)."""


class OrderCounts(Model):
    """Orders of a run by status; forced delisting exits counted apart."""

    total: int
    filled: int
    partial: int
    rejected: int
    rejected_by_reason: dict[str, int]
    forced: dict[str, int]
    """delist_write_down and delist_exit rows, not in the counts above."""


class DelistedRow(Model):
    """A delisted holding a run handled."""

    date: dt.date
    """The trading day at whose open it was handled."""
    code: str
    treatment: Literal["write_down", "exit"]
    shares: float
    value: float
    """Yuan at the last close."""
    cash: float
    """Yuan returned as cash (0 for a write-down without recovery)."""
