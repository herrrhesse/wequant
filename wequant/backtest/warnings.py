"""What a run relied on that its store does not vouch for."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from wequant.expr import STORED_AS, Expr, Field
from wequant.rules import default_book, limit_check_current
from wequant.rules.limits import down_price, up_price
from wequant.strategy import Choice, Section, Strategy, expressions

if TYPE_CHECKING:
    from wequant.account import Ledger
    from wequant.panel import Panel

WARNINGS = ("limits_unchecked", "st_unknown", "st_band_fills", "stale_holdings", "no_picks", "index_uncovered")
WARNINGS += ("list_days_approximate", "max_weight_cash")
STALE_DAYS = 20  # trading days without a bar after which a holding's last-close value is reported


def run_warnings(strategy: Strategy, panel: Panel, section: Section, choice: Choice, ledger: Ledger) -> dict[str, str]:
    """Each warning that applies, code -> text, in WARNINGS order."""
    found, fields = {}, _fields(expressions(strategy).values())
    if not limit_check_current(panel.manifest):
        found["limits_unchecked"] = (
            "the store's price limits are not checked against its bars; run tools.check_limits --commit"
        )
    known = _st_known_share(fields, panel, section)
    if known < 1:
        found["st_unknown"] = (
            f"ST state known on {known:.1%} of the universe cells on decision days; unknown reads as not ST"
        )
    buys, sells = st_band_fills(panel, ledger.orders)
    if buys or sells:
        found["st_band_fills"] = (
            f"{buys} buys at the +ST-band price and {sells} sells at the -ST-band price where ST is unknown"
        )
    stale, share, day = stale_holdings(panel, ledger)
    if stale:
        found["stale_holdings"] = (
            f"{stale} codes held on {day} last traded more than {STALE_DAYS} trading days earlier, valued at their "
            f"last close ({share:.1%} of equity)"
        )
    if not choice.target.any():
        found["no_picks"] = "no decision day selected a stock"
    if uncovered := index_uncovered(fields, panel, section):
        found["index_uncovered"] = "; ".join(
            f"member@{index}: {days} decision days outside its index_members span"
            + (f" from {first}" if first else ", which is empty")
            for index, days, first in uncovered
        )
    if "list_days" in fields:
        codes, cells = list_days_bounds(panel, section)
        found["list_days_approximate"] = (
            "list_days counts trading days from securities.first_date, the first day of the source's data, not the "
            f"listing date: a lower bound for the {codes} codes whose data starts with the calendar, which "
            f"min_list_days left out of {cells} decision-day cells"
        )
    if (kept := choice.cash[choice.cash > 0]).size:
        found["max_weight_cash"] = (
            f"{kept.size} decision days kept {kept.mean():.1%} of equity in cash on average: max_weight capped every "
            "target"
        )
    return found


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
    left_out = section.steps["min_list_days"][:, at_start] == 0 if "min_list_days" in section.steps else np.zeros(0)
    return int(at_start.sum()), int(left_out.sum())


def stale_holdings(panel: Panel, ledger: Ledger) -> tuple[int, float, dt.date]:
    """Codes held on the last day whose last bar lies more than STALE_DAYS rows earlier, their share of equity, the day."""
    day = ledger.daily["date"][-1]
    held = ledger.positions.filter(pl.col("date") == day)
    if held.is_empty():
        return 0, 0.0, day
    last = panel.row(day)
    traded = panel.traded[: last + 1, _columns(panel, held["code"])]
    since = np.argmax(traded[::-1], axis=0)  # rows since the last bar; 0 for a column that never traded
    stale = ~traded.any(axis=0) | (since > STALE_DAYS)
    return int(stale.sum()), float(held["value"].to_numpy()[stale].sum() / ledger.daily["equity"][-1]), day


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
    index = {code: j for j, code in enumerate(panel.codes.tolist())}
    return np.array([index[code] for code in codes], dtype=np.int64)
