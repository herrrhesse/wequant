"""Comparisons that become data_issues: new bars against the store, events against events, prices against factors."""

from __future__ import annotations

import polars as pl

from wequant.data.issues import issue_rows

VOLUME_RTOL = 1e-6  # the seed's volumes carry float32 rounding below this
AMOUNT_RTOL = 1e-5
STEP_RTOL = 1e-3  # the seed's ratios come from adjclose rounded to 0.01 元
PRICE_ATOL = 10  # 厘: the exchange rounds preclose to the fen
FLAGGED = ("event_missing", "event_unimplied", "event_step_off")

KEY = ["code", "date"]
OHLC = ("open", "high", "low", "close")
COMPARED = (*OHLC, "volume", "amount", "preclose")
STORED = (*COMPARED, "preclose_derived", "source")
EVENT = ("ex_date", "factor", "step")
ROUNDING = {"implied_step": 6, "factor_step": 6, "step": 6, "store_step": 6, "volume_rel": 9, "amount_rel": 9}


def audit_bars(
    new: pl.DataFrame, old: pl.DataFrame, status: pl.DataFrame, securities: pl.DataFrame, source: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(issues, compared): bars added or unconfirmed, and other volume, amount or exchange preclose on audited days."""
    store = old.select(*KEY, *(pl.col(c).alias(f"store_{c}") for c in STORED))
    values = new.select(*KEY, *COMPARED)
    compared = values.join(store.drop("store_source"), on=KEY)
    compared = compared.with_columns(volume_rel=_relative("volume"), amount_rel=_relative("amount"))
    issues = [
        _added(new.join(old, on=KEY, how="anti"), securities, source),
        _unconfirmed(store.join(new, on=KEY, how="anti"), status, source),
        *_mismatches(compared, source),
    ]
    return pl.concat(issues), compared


def audit_events(new: pl.DataFrame, old: pl.DataFrame, source: str) -> pl.DataFrame:
    """Issues for events only in the store, only in the new factors, or with steps apart by more than STEP_RTOL."""
    keys = ["code", "day"]
    new, old = new.filter(pl.col("day").is_not_null()), old.filter(pl.col("day").is_not_null())
    both = new.join(old.select(*keys, *(pl.col(c).alias(f"store_{c}") for c in EVENT)), on=keys)
    off = both.filter((pl.col("step") / pl.col("store_step") - 1).abs() > STEP_RTOL)
    events = [
        (old.join(new, on=keys, how="anti"), "event_unconfirmed", "event_dropped", EVENT),
        (new.join(old, on=keys, how="anti"), "event_added", "event_added", EVENT),
        (off, "step_mismatch", "value_replaced", ("ex_date", "step", "store_ex_date", "store_step")),
    ]
    return pl.concat(
        issue_rows(_rounded(rows.rename({"day": "date"})), "adj_factor", kind, action, detail, source)
        for rows, kind, action, detail in events
    )


def check_adjustment(bars: pl.DataFrame, adj: pl.DataFrame, source: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(issues, checked): each exchange-preclose bar with an earlier bar, its factor step against the step its preclose implies."""
    same_code = pl.col("code") == pl.col("code").shift(1)
    rows = (
        bars.lazy()
        .sort(KEY)
        .with_columns(
            prev_date=pl.when(same_code).then(pl.col("date").shift(1)),
            prev_close=pl.when(same_code).then(pl.col("close").shift(1)),
        )
        .filter(~pl.col("preclose_derived") & pl.col("prev_date").is_not_null())
        .collect(engine="streaming")
    )
    rows = _factor_on(_factor_on(rows, adj, "date", "f_t"), adj, "prev_date", "f_p")
    factor_step = pl.col("f_t") / pl.col("f_p")
    implied_step = pl.col("prev_close") / pl.col("preclose")
    by_ratio = (factor_step / implied_step - 1).abs() <= STEP_RTOL
    by_price = ((pl.col("prev_close") / factor_step).round(0) - pl.col("preclose")).abs() <= PRICE_ATOL
    checked = (
        rows.with_columns(
            factor_step=factor_step,
            implied_step=implied_step,
            agrees=by_ratio | by_price,
            ex_date=pl.when(pl.col("f_t_ex_date") > pl.col("prev_date")).then(pl.col("f_t_ex_date")),
        )
        .select(*KEY, "prev_date", "prev_close", "preclose", "implied_step", "factor_step", "ex_date", "agrees")
        .sort(KEY)
    )
    return _flagged(checked.filter(~pl.col("agrees")), source), checked


def status_disagreements(bars: pl.DataFrame, status: pl.DataFrame) -> pl.DataFrame:
    """Status rows whose tradestatus disagrees with whether the day has a bar, plus bars without a status row."""
    joined = status.select(*KEY, "tradestatus").join(
        bars.select(*KEY, "volume", bar=pl.lit(True)), on=KEY, how="full", coalesce=True
    )
    bar = pl.col("bar").fill_null(False)
    disagree = pl.col("tradestatus").is_null() | (bar != (pl.col("tradestatus") == 1))
    action = pl.when(bar).then(pl.lit("bar_kept")).otherwise(pl.lit("no_bar"))
    return joined.filter(disagree).select(*KEY, "tradestatus", "volume", action=action).sort(KEY)


def _relative(column: str) -> pl.Expr:
    new, old = pl.col(column), pl.col(f"store_{column}")
    return pl.when(old != 0).then((new - old).abs() / old.abs())


def _added(rows: pl.DataFrame, securities: pl.DataFrame, source: str) -> pl.DataFrame:
    rows = rows.join(securities.select("code", "first_date", "last_date"), on="code", how="left")
    position = (
        pl.when(pl.col("first_date").is_null())
        .then(pl.lit("new_code"))
        .when(pl.col("date") < pl.col("first_date"))
        .then(pl.lit("before_first"))
        .when(pl.col("date") > pl.col("last_date"))
        .then(pl.lit("after_last"))
        .otherwise(pl.lit("inside"))
    )
    return issue_rows(rows.with_columns(position=position), "bars", "bar_added", "row_added", ["position"], source)


def _unconfirmed(rows: pl.DataFrame, status: pl.DataFrame, source: str) -> pl.DataFrame:
    rows = rows.join(status.select(*KEY, "tradestatus"), on=KEY, how="left")
    detail = [f"store_{c}" for c in STORED if c != "preclose_derived"] + ["tradestatus"]
    return issue_rows(rows, "bars", "bar_unconfirmed", "row_dropped", detail, source)


def _mismatches(compared: pl.DataFrame, source: str) -> list[pl.DataFrame]:
    """Volume, amount and exchange-preclose differences; price differences are conflicts for the resolutions."""
    preclose = ~pl.col("store_preclose_derived") & pl.col("preclose").ne_missing(pl.col("store_preclose"))
    found = [
        (
            "volume_mismatch",
            compared.filter(pl.col("volume_rel") > VOLUME_RTOL),
            ["volume", "store_volume", "volume_rel"],
        ),
        (
            "amount_mismatch",
            compared.filter(pl.col("amount_rel") > AMOUNT_RTOL),
            ["amount", "store_amount", "amount_rel"],
        ),
        ("preclose_mismatch", compared.filter(preclose), ["preclose", "store_preclose"]),
    ]
    return [issue_rows(_rounded(rows), "bars", kind, "value_replaced", detail, source) for kind, rows, detail in found]


def _factor_on(rows: pl.DataFrame, adj: pl.DataFrame, on: str, name: str) -> pl.DataFrame:
    """rows plus the factor in force on column on as name (1.0 before a code's first row) and its ex_date as name_ex_date."""
    ex_date = f"{name}_ex_date"
    factors = adj.select("code", pl.col("ex_date").alias(ex_date), pl.col("factor").alias(name)).sort(ex_date)
    joined = rows.sort(on).join_asof(
        factors, left_on=on, right_on=ex_date, by="code", coalesce=False, check_sortedness=False
    )
    return joined.with_columns(pl.col(name).fill_null(1.0))


def _flagged(rows: pl.DataFrame, source: str) -> pl.DataFrame:
    kind = (
        pl.when((pl.col("factor_step") - 1).abs() <= STEP_RTOL)
        .then(pl.lit(FLAGGED[0]))
        .when(pl.col("preclose") == pl.col("prev_close"))
        .then(pl.lit(FLAGGED[1]))
        .otherwise(pl.lit(FLAGGED[2]))
    )
    rows = _rounded(rows.with_columns(kind=kind))
    detail = ["prev_date", "prev_close", "preclose", "implied_step", "factor_step", "ex_date"]
    return pl.concat(
        issue_rows(rows.filter(pl.col("kind") == k), "adj_factor", k, "flagged", detail, source) for k in FLAGGED
    )


def _rounded(rows: pl.DataFrame) -> pl.DataFrame:
    """Steps rounded to 6 places and relative errors to 9, as issue details show them."""
    return rows.with_columns(pl.col(c).round(n) for c, n in ROUNDING.items() if c in rows.columns)
