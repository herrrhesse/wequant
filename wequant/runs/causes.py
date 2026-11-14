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
    "stop_loss",  # sold by a sell rule (S4.2): the close fell drop below cost; drop, cost, close, first
    "take_profit",  # the close rose gain above cost; gain, cost, close, first
    "trailing_stop",  # the close fell drop below its highest since bought; drop, peak, close, first
    "max_hold",  # held days, the holding period limit; days, limit, first
    "sell_formula",  # the sell formula held; name, period, first
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
    "stop_loss": ("drop", "cost", "close", "first"),
    "take_profit": ("gain", "cost", "close", "first"),
    "trailing_stop": ("drop", "peak", "close", "first"),
    "max_hold": ("days", "limit", "first"),
    "sell_formula": ("name", "period", "first"),
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
    rows = rows.join(_exits(run), on=key, how="left")
    return rows.sort("order").select(
        *run.ledger.orders.columns,
        cause=_cause(),
        rank=pl.coalesce("sell_rank", "rank"),
        limit=pl.coalesce("rule_limit", "sell_limit"),
        step="sell_step",
        weight="weight",
        drift=pl.col("held").fill_null(0.0) / pl.col("equity") - pl.col("weight"),
        drop=pl.when(pl.col("rule").is_in(["stop_loss", "trailing_stop"])).then("rule_value"),
        gain=pl.when(pl.col("rule") == "take_profit").then("rule_value"),
        **{name: name for name in ("cost", "close", "peak", "days", "name", "period", "first")},
    )


def _exits(run: Run) -> pl.DataFrame:
    """The run's sell-rule marks by decision day and code (the holding engine's, S4.2); none for another engine."""
    schema = {"decided": pl.Date, "code": pl.String, "rule": pl.String, "first": pl.Date, "rule_value": pl.Float64}
    schema |= {"cost": pl.Float64, "close": pl.Float64, "peak": pl.Float64, "days": pl.Int64, "rule_limit": pl.Int64}
    schema |= {"name": pl.String, "period": pl.String}
    if run.exits is None:
        return pl.DataFrame(schema=schema)
    return run.exits.rename({"date": "decided", "limit": "rule_limit", "value": "rule_value"}).select(list(schema))


def _cause() -> pl.Expr:
    sold = pl.col("sell_kind").cast(pl.String).replace_strict(SELLS, default=None)
    return (
        pl.when(pl.col("reason").is_in(FORCED))
        .then(pl.lit("delisting"))
        .when(pl.col("rule").is_not_null() & (pl.col("side") == "sell"))
        .then(pl.col("rule"))
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
