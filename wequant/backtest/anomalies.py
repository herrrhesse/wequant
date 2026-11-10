"""Which anomalous cells a run used: read in a decision's signal window, filled on, or valued on.

Only decisions with a consequence count (a buy, keep or sell; a screened stock with a score), each by its own stock's
cells: a cross-sectional operator also reads other stocks' cells, which is not tracked. A signal window counts bars, so
it understates a reach that skips missing inputs (wequant.expr.lookback).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.account.ledger import FORCED
from wequant.analytics.inputs import EXECUTED
from wequant.backtest.messages import Params
from wequant.data.schema import CLASSES, LEVELS
from wequant.expr.lookback import fields, lookback
from wequant.strategy import ACTIONS, Choice, Section, Strategy, actions, expressions

if TYPE_CHECKING:
    from wequant.account import Ledger
    from wequant.panel import Panel

CHANNELS = ("signal", "fill", "valuation")
DECISIONS = ("buy", "keep", "sell", "target", "backup", "candidate")  # a run's actions, then a screen's roles
ANOMALY_USES = {
    "date": pl.Date,  # the decision day, the fill day or the valued close
    "code": pl.String,
    "channel": pl.Enum(CHANNELS),
    "anomaly_date": pl.Date,
    "kind": pl.String,
    "class": pl.Enum(CLASSES),
    "level": pl.Enum(LEVELS),
    "root": pl.String,  # signal: the first root that read the cell
    "full_history": pl.Boolean,  # signal: that root reads a stock's whole history
    "decision": pl.Enum(DECISIONS),  # signal
    "order_status": pl.String,  # fill: filled, partial, or the reason a rejected order gives
    "value": pl.Float64,  # valuation: yuan of the holding
    "share": pl.Float64,  # valuation: of equity
    "seq": pl.Int64,  # fill: the order's row in the orders frame; 0 elsewhere
}
ORDER = ("date", "code", "channel", "anomaly_date", "kind", "seq")  # a total order: rerun bytes are equal
PRICE_READS = frozenset(("open", "high", "low", "close", "preclose", "limit_up_price", "limit_down_price"))
LIMIT_READS = frozenset(("limit_up_price", "limit_down_price", "limit_up", "limit_down", "limit_locked", "limit_known"))
ADJUSTED = frozenset(f"adj_{name}" for name in ("open", "high", "low", "close", "preclose"))
READS = {  # the fields whose values a class taints; bar and status taint every field
    "price": PRICE_READS | LIMIT_READS | ADJUSTED,
    "volume": frozenset(("volume", "amount")),
    "limits": LIMIT_READS,
    "adjustment": ADJUSTED | {"factor"},
}
FILLED_ON = ("price", "volume", "bar", "gap", "limits", "status")  # volume only by an executed order
VALUED_ON = ("price", "bar", "gap", "status")  # adjustment by a holding across the day's open


def run_uses(strategy: Strategy, panel: Panel, section: Section, choice: Choice, ledger: Ledger) -> pl.DataFrame:
    """ANOMALY_USES of a run, in ORDER: the run's actions, its orders and its holdings on the cells it did not exclude."""
    cells = panel.anomalies.filter(~pl.col("excluded"))
    if cells.is_empty():
        return pl.DataFrame(schema=ANOMALY_USES)
    found = [
        signal_uses(strategy, panel, run_decisions(section, choice, cells["col"].unique().to_numpy()), cells),
        fill_uses(cells, ledger.orders),
        valuation_uses(cells, ledger),
    ]
    return pl.concat([_as_uses(frame) for frame in found]).sort(ORDER)


def run_decisions(section: Section, choice: Choice, cols: np.ndarray) -> pl.DataFrame:
    """The buys, keeps and sells of the decision rows in columns cols: row, col and decision."""
    action = actions(choice.target[:, cols], choice.held[:, cols], section.traded[:, cols])
    i, k = np.nonzero(np.isin(action, [ACTIONS.index(name) for name in DECISIONS[:3]]))
    return pl.DataFrame({"row": section.rows[i], "col": cols[k], "decision": np.array(ACTIONS)[action[i, k]]})


def signal_uses(strategy: Strategy, panel: Panel, decisions: pl.DataFrame, cells: pl.DataFrame) -> pl.DataFrame:
    """A use per decision (row, col, decision) and cell of its stock in the window of a root that reads its class,
    named by the first such root; a window holds a root's lookback + 1 bars up to the decision."""
    pairs = decisions.join(cells.rename({"row": "anomaly_row", "date": "anomaly_date"}), on="col")
    pairs = pairs.filter(pl.col("row") >= pl.col("anomaly_row"))
    if pairs.is_empty():
        return pl.DataFrame(schema=ANOMALY_USES)
    t, d, before = _bar_counts(panel, pairs)
    roots = [(name, lookback(expr), _classes(fields(expr))) for name, expr in expressions(strategy).items()]
    roots = [root for root in roots if root[0] != "pool"]  # its universe and items are roots of their own
    classes = pairs["class"].to_numpy()
    hits = np.stack([_read(classes, t, d, before, reach, read) for _, reach, read in roots])
    first, used = hits.argmax(axis=0), hits.any(axis=0)
    names, whole = np.array([name for name, _, _ in roots]), np.array([reach is None for _, reach, _ in roots])
    found = pairs.filter(pl.Series(used))
    return found.with_columns(
        date=pl.Series(panel.dates).gather(found["row"]),
        channel=pl.lit("signal"),
        root=pl.Series(names[first[used]]),
        full_history=pl.Series(whole[first[used]]),
        seq=pl.lit(0),
    )


def fill_uses(cells: pl.DataFrame, orders: pl.DataFrame) -> pl.DataFrame:
    """A use per rebalance order on an anomalous cell whose class a fill reads; volume only for an executed order."""
    orders = orders.with_row_index("seq").filter(~pl.col("reason").is_in(FORCED).fill_null(False))
    found = orders.join(
        cells.rename({"date": "anomaly_date"}), left_on=["date", "code"], right_on=["anomaly_date", "code"]
    )
    executed = pl.col("status").is_in(EXECUTED)
    status = pl.when(pl.col("status") == "rejected").then(pl.col("reason")).otherwise(pl.col("status"))
    return found.filter(pl.col("class").is_in(FILLED_ON) & ((pl.col("class") != "volume") | executed)).with_columns(
        anomaly_date=pl.col("date"), channel=pl.lit("fill"), order_status=status, seq=pl.col("seq").cast(pl.Int64)
    )


def valuation_uses(cells: pl.DataFrame, ledger: Ledger) -> pl.DataFrame:
    """A use per holding valued on an anomalous close, or across an adjustment day's open (valued the close before)."""
    daily = ledger.daily.select("date", "equity", following=pl.col("date").shift(-1))
    held = ledger.positions.join(daily, on="date").with_columns(share=pl.col("value") / pl.col("equity"))
    cells = cells.rename({"date": "anomaly_date"})
    on_day = held.join(
        cells.filter(pl.col("class").is_in(VALUED_ON)), left_on=["date", "code"], right_on=["anomaly_date", "code"]
    )
    across = held.join(
        cells.filter(pl.col("class") == "adjustment"), left_on=["following", "code"], right_on=["anomaly_date", "code"]
    )
    return pl.concat(
        [
            on_day.with_columns(anomaly_date=pl.col("date")),
            across.with_columns(date=pl.col("following"), anomaly_date=pl.col("following")),
        ]
    ).with_columns(channel=pl.lit("valuation"), seq=pl.lit(0))


def anomaly_params(uses: pl.DataFrame, excluded: tuple[str, ...]) -> Params:
    """data_anomaly's params: codes and cells used, cells by level, decisions that read them, orders on them, the
    largest holding on them, and with excluded levels the uses of those no missing day removes."""
    cells = uses.select("code", "anomaly_date", "kind", "level").unique()
    by_level = dict(cells["level"].cast(pl.String).value_counts().iter_rows())
    valued = uses.filter(pl.col("channel") == "valuation").sort("share", "date", descending=[True, False])
    worst = valued.row(0, named=True) if valued.height else {"share": None, "date": None}
    return {
        "codes": uses["code"].n_unique(),
        "cells": cells.height,
        **{f"{level}_cells": by_level.get(level, 0) for level in LEVELS},
        "decisions": uses.filter(pl.col("channel") == "signal").select("date", "code").unique().height,
        "fills": uses.filter(pl.col("channel") == "fill").height,
        "max_share": worst["share"],
        "max_share_day": worst["date"] and worst["date"].isoformat(),
        "unavoidable": uses.filter(pl.col("level").cast(pl.String).is_in(list(excluded))).height if excluded else None,
    }


def _as_uses(frame: pl.DataFrame) -> pl.DataFrame:
    """frame's ANOMALY_USES columns, null where it has none."""
    columns = [pl.col(name) if name in frame.columns else pl.lit(None).alias(name) for name in ANOMALY_USES]
    return frame.select(columns).cast(ANOMALY_USES)


def _bar_counts(panel: Panel, pairs: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each pair's stock's bars up to its decision row, up to its anomaly row, and before its anomaly row."""
    cols = pairs["col"].unique().to_numpy()
    local = np.searchsorted(cols, pairs["col"].to_numpy())
    bars = np.zeros((len(panel.dates) + 1, len(cols)), dtype=np.int32)  # bars[r + 1]: bars in rows 0 .. r
    bars[1:] = np.cumsum(panel.traded[:, cols], axis=0)
    row, anomaly = pairs["row"].to_numpy(), pairs["anomaly_row"].to_numpy()
    return bars[row + 1, local], bars[anomaly + 1, local], bars[anomaly, local]


def _classes(names: frozenset[str]) -> frozenset[str]:
    """The classes whose taint a root reading names sees."""
    tainted = {name for name, read in READS.items() if names & read}
    return frozenset(tainted | ({"bar", "status"} if names else set()))


def _read(
    classes: np.ndarray, t: np.ndarray, d: np.ndarray, before: np.ndarray, reach: int | None, read: frozenset[str]
) -> np.ndarray:
    """Whether a root with lookback reach that sees the read classes reads each pair's cell: its window holds the
    cell's day, or for an adjustment bars on both sides of it."""
    first = np.ones_like(t) if reach is None else np.maximum(t - reach, 1)  # the window's first bar by number
    holds = (d >= first) & np.isin(classes, [c for c in read if c != "adjustment"])
    spans = (before >= first) & (t > before) & (classes == "adjustment") & ("adjustment" in read)
    return holds | spans
