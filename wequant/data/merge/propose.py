"""Proposed resolutions by the user's rules, for the conflicts on the raw days; reads only."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from itertools import groupby

import polars as pl

from wequant.data.merge.assemble import BJ, last_bars, previous_bars, scan_years
from wequant.data.merge.audit import OHLC
from wequant.data.merge.conflicts import PRICE_KINDS, price_conflicts, with_references
from wequant.data.merge.daily import read_daily, split
from wequant.data.merge.detect import marks
from wequant.data.merge.reconcile import drop_duplicate_codes
from wequant.data.merge.resolutions import ENTRY_KEY, SCHEMA, frame
from wequant.data.merge.resolve import resolve
from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store

TRADED = ("volume", "amount")
OUTSIDE = "check an outside source"


def propose(
    store: Store, raw: BaoStockRaw, days: Iterable[dt.date] | None = None, entries: pl.DataFrame | None = None
) -> list[dict]:
    """A proposed entry for each conflict on these raw days (None: every day on disk) whose reference is fetched, one
    year at a time; the amount check sees the prices a price entry of entries takes, else the proposal's."""
    manifest = store.manifest()
    days = sorted(raw.daily_days() if days is None else days)
    entries = pl.DataFrame(schema=SCHEMA) if entries is None else entries
    calendar = store.read_table("calendar", manifest=manifest)["date"]
    adj = store.read_table("adj_factor", manifest=manifest)
    proposals, previous = [], previous_bars(store, manifest, days[0]) if days else None
    for _, year in groupby(days, lambda d: d.year):
        bars, status, old = _bars(store, manifest, raw, list(year))
        prices = with_references(raw, price_conflicts(bars, old), calendar, adj)
        ready = pl.col("fetched") | (pl.col("kind") != "price_mismatch")  # a price waits for its reference day
        decided = [proposal(row) for row in prices.filter(ready).to_dicts()]
        found = marks(bars, status, previous, old)
        _, _, unresolved = resolve(bars, old, prices, found, _prices_taken(decided, entries), "")
        missing = ~pl.col("kind").is_in(PRICE_KINDS) & (pl.col("entry") == "missing")
        proposals += decided + [proposal(row) for row in unresolved.filter(missing).to_dicts()]
        previous = last_bars(pl.concat([previous, bars.select(previous.columns)]))
    return sorted(proposals, key=lambda p: (p["date"], p["code"], p["kind"]))


def proposal(row: dict) -> dict:
    """The entry the rules propose for one conflict row (CONFLICT columns, plus REFERENCE ones for a price)."""
    fields, take, evidence = RULES[row["kind"]](row)
    entry = {"code": row["code"], "date": row["date"], "kind": row["kind"], "fields": fields, "take": take}
    rule = {"values": {}, "category": "rule", "level": "suspicious", "verifications": ()}
    return entry | rule | {"evidence": evidence, "status": "proposed", "approval": ""}


def _bars(store: Store, manifest: dict, raw: BaoStockRaw, days: list[dt.date]) -> tuple[pl.DataFrame, ...]:
    """(these days' new bars and status rows, all in one year, duplicate codes dropped as the merge drops them; the
    store's bars)."""
    bars, status = split(read_daily(raw, days))
    old = scan_years(store, manifest, "bars", {days[0].year}).filter(~BJ & pl.col("date").is_in(days)).collect()
    bars, status, _ = drop_duplicate_codes(bars, status, old, "")
    return bars, status, old


def _prices_taken(proposals: list[dict], entries: pl.DataFrame) -> pl.DataFrame:
    """The price entries the amount check applies, as approved: those in entries, approved or not (add never replaces
    them), else the proposals; an undetermined one leaves its cell's amounts waiting."""
    filed = entries.filter(pl.col("kind") == "price_mismatch")
    mine = frame(proposals).join(filed, on=ENTRY_KEY, how="anti")
    taken = pl.concat([filed, mine]).filter(pl.col("take") != "undetermined")
    return taken.with_columns(status=pl.lit("approved"))


def _price(row: dict) -> tuple[tuple, str, str]:
    """BaoStock's prices when its close equals the next day's exchange preclose, the seed's when the seed's close does;
    undetermined when that cannot tell them apart: equal closes, no preclose, an adj_factor step by then, neither."""
    closes = f"BaoStock close {_yuan(row['close'])}, seed close {_yuan(row['seed_close'])}"
    others = [f"{c} {_yuan(row[c])} vs {_yuan(row[f'seed_{c}'])}" for c in OHLC[:3] if row[c] != row[f"seed_{c}"]]
    also = f"; also differ (BaoStock vs seed): {', '.join(others)}" if others else ""
    reference, day = row["next_preclose"], row["next_date"]
    stated = f"the {day} exchange preclose {_yuan(reference)}"
    if row["close"] == row["seed_close"]:
        take, verdict = "undetermined", f"equal closes, so the next day's exchange preclose cannot decide: {OUTSIDE}"
    elif reference is None:
        take, verdict = "undetermined", f"the {day} snapshot has no exchange preclose for the code: {OUTSIDE}"
    elif row["ex_event"]:
        take, verdict = "undetermined", f"{stated}, but an adj_factor step by then spoils the comparison: {OUTSIDE}"
    elif row["close"] == reference:
        take, verdict = "baostock", f"{stated} repeats BaoStock's"
    elif row["seed_close"] == reference:
        take, verdict = "seed", f"{stated} repeats the seed's"
    else:
        take, verdict = "undetermined", f"{stated} repeats neither: {OUTSIDE}"
    return OHLC, take, f"{closes}; {verdict}{also}"


def _preclose(row: dict) -> tuple[tuple, str, str]:
    """Undetermined: only the exchange can say whether a preclose off the fen is right."""
    return (
        ("preclose",),
        "undetermined",
        f"BaoStock exchange preclose {_yuan(row['preclose'])} is not whole fen: {OUTSIDE}",
    )


def _stale(row: dict) -> tuple[tuple, str, str]:
    """No bar: a suspended row carries no trade, whatever volume it repeats."""
    return (
        (),
        "no_bar",
        f"tradestatus 0 and amount 0 with volume {row['volume']}: a suspended day's stale volume, no bar",
    )


def _truncated(row: dict) -> tuple[tuple, str, str]:
    """The seed's volume and amount: the day's snapshot was cut, and the row's volume is short of the seed's."""
    share = f"{row['volume'] / row['seed_volume']:.1%}"
    stated = f"BaoStock {_traded(row, '')} is {share} of the seed's {_traded(row, 'seed_')} on a cut snapshot"
    if row["seed_amount"] is None:
        return TRADED, "undetermined", f"{stated}, and the seed has no amount: {OUTSIDE}"
    return TRADED, "seed", stated


def _carried(row: dict) -> tuple[tuple, str, str]:
    """The seed's amount when the seed bar has the same volume and passes the check; else undetermined."""
    stated = f"BaoStock amount {row['amount']:.2f} repeats its previous bar's at volume {row['volume']}"
    if row["seed_close"] is None:
        return ("amount",), "undetermined", f"{stated}; no seed bar: {OUTSIDE}"
    seed = f"seed {_traded(row, 'seed_')}"
    if row["same_volume"] and row["seed_paid"]:
        return ("amount",), "seed", f"{stated}; {seed}, same volume, passes the check"
    return ("amount",), "undetermined", f"{stated}; {seed} does not settle it: {OUTSIDE}"


def _average(row: dict) -> tuple[tuple, str, str]:
    """The seed's amount (same bar) or volume and amount (other volume) when the seed bar passes the check; else null."""
    baostock = f"BaoStock {_traded(row, '')}"
    span = f"[{_yuan(row['low'])}, {_yuan(row['high'])}]"
    if row["seed_close"] is None:
        return ("amount",), "null", f"{baostock} outside {span} and no seed bar: amount null, bar kept"
    same_prices = all(row[c] == row[f"seed_{c}"] for c in OHLC)
    if (row["seed_low"], row["seed_high"]) != (row["low"], row["high"]):
        span += f", seed [{_yuan(row['seed_low'])}, {_yuan(row['seed_high'])}]"
    stated = f"{baostock}, seed {_traded(row, 'seed_')} against {span}"
    if not row["seed_paid"]:
        return ("amount",), "null", f"{stated}: the seed bar fails the check too, amount null, bar kept"
    if not row["same_volume"]:
        return ("volume", "amount"), "seed", f"{stated}: the volumes differ and the seed bar passes"
    if same_prices:
        return ("amount",), "seed", f"{stated}: same bar, the seed amount passes"
    return ("amount",), "null", f"{stated}: same volume but other prices, amount null, bar kept"


RULES = {
    "price_mismatch": _price,
    "preclose_odd": _preclose,
    "average_price": _average,
    "amount_carried": _carried,
    "stale_volume": _stale,
    "volume_truncated": _truncated,
}


def _traded(row: dict, prefix: str) -> str:
    """'volume V amount A (average P)' of one source; 'none' for an amount or average the row lacks."""
    volume, amount = row[f"{prefix}volume"], row[f"{prefix}amount"]
    average = f"{amount / volume:.4f}" if volume and amount is not None else "none"
    money = "none" if amount is None else f"{amount:.2f}"
    return f"volume {volume} amount {money} (average {average})"


def _yuan(li: int | None) -> str:
    """厘 as yuan with two decimals, three when the value is not whole fen."""
    if li is None:
        return "none"
    return f"{li / 1000:.2f}" if li % 10 == 0 else f"{li / 1000:.3f}"
