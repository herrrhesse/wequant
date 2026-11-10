"""What a run relied on that its store does not vouch for."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.backtest.messages import TEXTS, Params, warning
from wequant.expr import STORED_AS, Expr, Field
from wequant.rules import default_book, limit_check_current
from wequant.rules.limits import down_price, up_price
from wequant.strategy import Choice, Section, Strategy, expressions

if TYPE_CHECKING:
    from wequant.account import Ledger
    from wequant.panel import Panel

WARNINGS = tuple(TEXTS)  # the order a run's warnings come in
STALE_DAYS = 20  # trading days without a bar after which a holding's last-close value is reported


def run_warnings(
    strategy: Strategy, panel: Panel, section: Section, choice: Choice, ledger: Ledger
) -> dict[str, tuple[str, Params]]:
    """Each warning that applies, code -> (text, params), in WARNINGS order."""
    found, fields = {}, _fields(expressions(strategy).values())
    if not limit_check_current(panel.manifest):
        found["limits_unchecked"] = {}
    if (known := _st_known_share(fields, panel, section)) < 1:
        found["st_unknown"] = {"known_share": known}
    buys, sells = st_band_fills(panel, ledger.orders)
    if buys or sells:
        found["st_band_fills"] = {"buys": buys, "sells": sells}
    if (stale := stale_holdings(panel, ledger))["codes"]:
        found["stale_holdings"] = stale
    if not choice.target.any():
        found["no_picks"] = {}
    if uncovered := index_uncovered(fields, panel, section):
        found["index_uncovered"] = _numbered(uncovered)
    if "list_days" in fields:
        codes, cells = list_days_bounds(panel, section)
        found["list_days_approximate"] = {"codes_at_calendar_start": codes, "cells": cells}
    if (kept := choice.cash[choice.cash > 0]).size:
        found["max_weight_cash"] = {"days": kept.size, "mean_share": float(kept.mean())}
    return {code: warning(code, params) for code, params in found.items()}


def index_uncovered(fields: set[str], panel: Panel, section: Section) -> list[tuple[str, int, dt.date | None]]:
    """(index, decision days outside its index_members span, the span's first day) of each member@ index read that
    misses a decision day; the first day is None when the store has no member of it."""
    found = []
    for index in sorted(name.partition("@")[2] for name in fields if name.startswith("member@")):
        stored = STORED_AS.get(index, index)
        days = int((panel.member(stored)[section.rows] == -1).all(axis=1).sum())
        if days:
            span = panel.index_span(stored)
            found.append((index, days, None if span is None else span[0]))
    return found


def list_days_bounds(panel: Panel, section: Section) -> tuple[int, int]:
    """Codes whose data starts with the calendar, so that list_days only bounds their listing age from below, and the
    decision-day cells of theirs min_list_days left out."""
    at_start = panel.list_days[0] == panel.calendar_offset + 1
    if "min_list_days" not in section.steps:
        return int(at_start.sum()), 0
    return int(at_start.sum()), int((section.steps["min_list_days"][:, at_start] == 0).sum())


def stale_holdings(panel: Panel, ledger: Ledger) -> Params:
    """Codes held at a close of the run more than STALE_DAYS rows after their last bar, the worst day's share of equity
    in such holdings and the last day's."""
    day, positions = ledger.daily["date"][-1], ledger.positions
    stale = positions.filter(pl.Series(_stale(panel, positions))) if positions.height else positions
    by_day = (
        stale.group_by("date")
        .agg(pl.col("value").sum())
        .join(ledger.daily.select("date", "equity"), on="date")
        .select("date", share=pl.col("value") / pl.col("equity"))
        .sort("date")
    )
    worst = by_day.row(by_day["share"].arg_max(), named=True) if by_day.height else {"date": None, "share": 0.0}
    last = by_day.filter(pl.col("date") == day)["share"]
    return {
        "codes": stale["code"].n_unique(),
        "stale_days": STALE_DAYS,
        "share": last.item() if last.len() else 0.0,
        "day": day.isoformat(),
        "worst_share": worst["share"],
        "worst_day": worst["date"] and worst["date"].isoformat(),
    }


def _stale(panel: Panel, positions: pl.DataFrame) -> np.ndarray:
    """Whether each position's code had no bar in the STALE_DAYS + 1 rows up to its day."""
    rows, codes = np.searchsorted(panel.dates, positions["date"].to_numpy()), positions["code"]
    held = codes.unique(maintain_order=True)
    cols, local = _columns(panel, held), _columns_of(held, codes)
    lo = max(int(rows.min()) - STALE_DAYS, 0)
    bars = np.zeros((rows.max() + 2 - lo, len(held)), dtype=np.int32)  # bars[k]: bars in rows lo .. lo + k - 1
    bars[1:] = np.cumsum(panel.traded[lo : rows.max() + 1][:, cols], axis=0)
    first = np.maximum(rows - STALE_DAYS, lo)
    return bars[rows + 1 - lo, local] == bars[first - lo, local]


def st_band_fills(panel: Panel, orders: pl.DataFrame) -> tuple[int, int]:
    """Fills at the ST band's limit price on LIMITED main-board cells whose ST state is unknown and band narrower."""
    fills = orders.filter(pl.col("shares") > 0)
    t, j = np.searchsorted(panel.dates, fills["date"].to_numpy()), _columns(panel, fills["code"])
    st_bp, normal_bp, tick = main_band(panel.dates[t])
    limited = np.isfinite(panel.limit_up_price[t, j]) & (panel.boards[j] == "main")
    unknown = limited & ~panel.st_known[t, j] & (st_bp < normal_bp)
    up, down = band_prices(np.rint(panel.raw("preclose")[t, j] * 1000), st_bp, tick)
    price, sell = (fills["price"] * 1000).round().to_numpy(), fills["side"].to_numpy() == "sell"
    return int((unknown & ~sell & (price == up)).sum()), int((unknown & sell & (price == down)).sum())


def main_band(dates: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """st_bp, normal_bp and tick of the main board on each date."""
    book = default_book()
    bands = {d: book.in_force("band", "main", d) for d in set(dates.tolist())}
    lots = {d: book.in_force("lot", "main", d) for d in bands}
    days = dates.tolist()
    st, normal = (np.array([getattr(bands[d], name) for d in days], dtype=np.int64) for name in ("st_bp", "normal_bp"))
    return st, normal, np.array([lots[d].tick for d in days], dtype=np.int64)


def band_prices(pre: np.ndarray, bp: np.ndarray, tick: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Up and down limit in 厘 of preclose pre (厘) at band bp, by wequant.rules.limits; -1 where pre is NaN."""
    frame = pl.DataFrame({"pre": np.nan_to_num(pre, nan=-1).astype(np.int64), "bp": bp, "tick": tick})
    pre_, bp_, tick_ = pl.col("pre"), pl.col("bp"), pl.col("tick")
    out = frame.select(up=up_price(pre_, bp_, tick_), down=down_price(pre_, bp_, tick_))
    valid = ~np.isnan(pre)
    return np.where(valid, out["up"].to_numpy(), -1), np.where(valid, out["down"].to_numpy(), -1)


def _st_known_share(fields: set[str], panel: Panel, section: Section) -> float:
    """The share of universe cells on decision rows whose ST state is known; 1.0 unless an expression reads ST."""
    cells = section.steps["universe"] == 1
    if not fields & {"st", "st_known"} or not cells.any():
        return 1.0
    return float(panel.st_known[section.rows][cells].mean())


def _fields(roots: Iterable[Expr]) -> set[str]:
    """The names of the fields the expressions among roots read."""
    found, stack = set(), list(roots)
    while stack:
        node = stack.pop()
        if isinstance(node, Field):
            found.add(node.name)
        stack.extend(a for a in getattr(node, "args", ()) if isinstance(a, Expr))
    return found


def _columns(panel: Panel, codes: pl.Series) -> np.ndarray:
    return _columns_of(pl.Series(panel.codes), codes)


def _columns_of(order: pl.Series, codes: pl.Series) -> np.ndarray:
    """The position of each of codes in order."""
    index = {code: j for j, code in enumerate(order.to_list())}
    return np.array([index[code] for code in codes], dtype=np.int64)


def _numbered(uncovered: list[tuple[str, int, dt.date | None]]) -> Params:
    """index, days and first of the first uncovered index, index_2, days_2 and first_2 of the second, and so on."""
    params = {}
    for k, (index, days, first) in enumerate(uncovered, 1):
        suffix = "" if k == 1 else f"_{k}"
        params |= {f"index{suffix}": index, f"days{suffix}": days, f"first{suffix}": first and first.isoformat()}
    return params
