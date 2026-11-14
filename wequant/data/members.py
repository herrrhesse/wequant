"""Index member spans from BaoStock's member lists (round 3): a list dated L names the members from L on, so an update
grows the spans current on the store's last day and starts spans for new members."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import polars as pl

from wequant.data.schema import TABLES


def member_rows(
    spans: pl.DataFrame,
    index_code: str,
    members: Sequence[str],
    listed: dt.date,
    before: dt.date,
    days: Sequence[dt.date],
) -> pl.DataFrame:
    """The index_members rows of index_code to upsert once the trading days after before are merged, from the member
    list dated listed: a span current on before grows to the last day while its code stays a member, and to the day
    before listed once it left; a new member's span starts on the first of the days on or after listed."""
    listed = min(listed, days[-1])
    last, first = days[-1], next(d for d in days if d >= max(listed, days[0]))
    left = [d for d in days if d < listed]
    current = spans.filter((pl.col("index_code") == index_code) & (pl.col("end_date") == before))
    kept = set(members)
    grown = current.with_columns(
        end_date=pl.when(pl.col("code").is_in(kept)).then(pl.lit(last)).otherwise(pl.lit(left[-1] if left else before))
    ).filter(pl.col("end_date") != before)
    new = sorted(kept - set(current["code"]))
    started = pl.DataFrame(
        {
            "index_code": [index_code] * len(new),
            "code": new,
            "start_date": [first] * len(new),
            "end_date": [last] * len(new),
        },
        schema=TABLES["index_members"].schema,
    )
    return pl.concat([grown.select(started.columns).cast(started.schema), started])


def spans_from_lists(index_code: str, lists: dict[dt.date, set[str]], days: Sequence[dt.date]) -> pl.DataFrame:
    """index_members spans of index_code over the trading days from member lists by their dates: on each day the
    members are the list with the latest date on or before it (days before the first list have none)."""
    dated = sorted(lists)
    rows, open_spans = [], {}  # code -> (start, last day seen)
    for day in days:
        at = max((d for d in dated if d <= day), default=None)
        members = lists[at] if at is not None else set()
        for code in [c for c in open_spans if c not in members]:
            rows.append((index_code, code, *open_spans.pop(code)))
        for code in members:
            start = open_spans.get(code, (day,))[0]
            open_spans[code] = (start, day)
    rows += [(index_code, code, start, end) for code, (start, end) in open_spans.items()]
    frame = pl.DataFrame(rows, schema=TABLES["index_members"].schema, orient="row")
    return frame.sort("index_code", "code", "start_date")
