"""Why each order of a run was placed, from the run's own decisions (round 2): the decision day's action and sell
reason, the pick's rank, and how far a kept holding's weight had drifted from its target. An order without a decision
row of its code on the day before it reads as unrecorded."""

from __future__ import annotations

import polars as pl

from wequant.account.ledger import FORCED
from wequant.runs.run import Run

CAUSES = (
    "enter",  # a new target bought; params rank
    "backup",  # a backup bought in a target's place; rank
    "rebalance",  # a kept holding adjusted to its target weight; weight, drift
    "exit_pool",  # sold: out of the pool; step
    "exit_filter",  # sold: a filter fails; step
    "exit_no_score",  # sold: a factor without a value; step
    "exit_rank",  # sold: ranked out of the holding range; rank, limit
    "exit_slots",  # sold: the slots went to better-ranked stocks; rank, limit
    "delisting",  # cleared by the delisting rule
    "unrecorded",  # no decision row explains it
)
PARAMS = {  # the params each cause names
    "enter": ("rank",),
    "backup": ("rank",),
    "rebalance": ("weight", "drift"),
    "exit_pool": ("step",),
    "exit_filter": ("step",),
    "exit_no_score": ("step",),
    "exit_rank": ("rank", "limit"),
    "exit_slots": ("rank", "limit"),
    "delisting": (),
    "unrecorded": (),
}
SELLS = {"universe": "exit_pool", "filter": "exit_filter", "no_score": "exit_no_score", "rank": "exit_rank"}
SELLS |= {"slots": "exit_slots"}
DECIDED = pl.col("date").alias("decided")  # the decision day a frame row belongs to


def with_causes(run: Run) -> pl.DataFrame:
    """The run's orders in archive order with cause (a CAUSES code) and the columns its PARAMS read: rank, limit, step,
    weight and drift (held weight at the decision close minus the target weight)."""
    days = run.ledger.daily.select(decided="date", equity="equity").sort("decided")
    orders = run.ledger.orders.with_row_index("order").sort("date")
    orders = orders.join_asof(days, left_on="date", right_on="decided", strategy="backward", allow_exact_matches=False)
    key = ["decided", "code"]
    detail = run.detail.select(DECIDED, "code", "action", "rank", "sell_kind", "sell_step", "sell_rank", "sell_limit")
    picks = run.picks.select(DECIDED, "code", "role", "weight")
    held = run.ledger.positions.select(DECIDED, "code", held="value")
    rows = orders.join(detail, on=key, how="left").join(picks, on=key, how="left").join(held, on=key, how="left")
    return rows.sort("order").select(
        *run.ledger.orders.columns,
        cause=_cause(),
        rank=pl.coalesce("sell_rank", "rank"),
        limit="sell_limit",
        step="sell_step",
        weight="weight",
        drift=pl.col("held").fill_null(0.0) / pl.col("equity") - pl.col("weight"),
    )


def _cause() -> pl.Expr:
    sold = pl.col("sell_kind").cast(pl.String).replace_strict(SELLS, default=None)
    return (
        pl.when(pl.col("reason").is_in(FORCED))
        .then(pl.lit("delisting"))
        .when(pl.col("action") == "sell")
        .then(sold)
        .when(pl.col("action") == "buy")
        .then(pl.lit("enter"))
        .when(pl.col("action") == "keep")
        .then(pl.lit("rebalance"))
        .when(pl.col("role") == "backup")
        .then(pl.lit("backup"))
        .otherwise(pl.lit("unrecorded"))
        .fill_null("unrecorded")
    )
