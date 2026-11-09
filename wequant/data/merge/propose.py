"""Proposed resolutions by the user's rules, for every conflict on the raw days; reads only."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from itertools import groupby

import polars as pl

from wequant.data.merge.assemble import BJ, scan_years
from wequant.data.merge.conflicts import KEY, OHLC, find_conflicts, next_precloses
from wequant.data.merge.daily import read_daily, split
from wequant.data.merge.reconcile import drop_duplicate_codes
from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store


def propose(store: Store, raw: BaoStockRaw, days: Iterable[dt.date] | None = None) -> list[dict]:
    """A proposed entry for each conflict on these raw days (None: every day on disk), read one year at a time."""
    manifest = store.manifest()
    days = sorted(raw.daily_days() if days is None else days)
    if not days:
        return []
    conflicts = pl.concat(_conflicts(store, manifest, raw, list(year)) for _, year in groupby(days, lambda d: d.year))
    calendar = store.read_table("calendar", manifest=manifest)["date"]
    adj = store.read_table("adj_factor", manifest=manifest)
    references = next_precloses(raw, conflicts.filter(pl.col("kind") == "price_mismatch"), calendar, adj)
    rows = conflicts.join(references, on=KEY, how="left").sort("date", "code", "kind").to_dicts()
    return [proposal(row) for row in rows]


def proposal(row: dict) -> dict:
    """The entry the rules propose for one conflict row (find_conflicts columns plus next_precloses ones)."""
    fields, take, evidence = (_price if row["kind"] == "price_mismatch" else _average)(row)
    entry = {"code": row["code"], "date": row["date"], "kind": row["kind"], "fields": fields, "take": take}
    return entry | {"evidence": evidence, "status": "proposed"}


def _conflicts(store: Store, manifest: dict, raw: BaoStockRaw, days: list[dt.date]) -> pl.DataFrame:
    """find_conflicts of these days' new bars (all in one year), duplicate codes dropped as the merge drops them."""
    bars, status = split(read_daily(raw, days))
    old = scan_years(store, manifest, "bars", {days[0].year}).filter(~BJ & pl.col("date").is_in(days)).collect()
    bars, _, _ = drop_duplicate_codes(bars, status, old, "")
    return find_conflicts(bars, old)


def _price(row: dict) -> tuple[tuple, str, str]:
    """BaoStock's prices when its close equals the next day's exchange preclose, the seed's when the seed's close does;
    BaoStock's with the cell flagged otherwise."""
    closes = f"BaoStock close {_yuan(row['close'])}, seed close {_yuan(row['seed_close'])}"
    others = [f"{c} {_yuan(row[c])} vs {_yuan(row[f'seed_{c}'])}" for c in OHLC[:3] if row[c] != row[f"seed_{c}"]]
    also = f"; also differ (BaoStock vs seed): {', '.join(others)}" if others else ""
    reference, day = row["next_preclose"], row["next_date"]
    stated = (
        f"the {day} exchange preclose {_yuan(reference)}" if reference is not None else f"no {day} exchange preclose"
    )
    flagged = "BaoStock kept and the cell flagged"
    if row["ex_event"]:
        take, verdict = "baostock", f"{stated}, but an adj_factor step by then spoils the comparison: {flagged}"
    elif reference is None:
        take, verdict = "baostock", f"{stated}: {flagged}"
    elif row["close"] == reference:
        take, verdict = "baostock", f"{stated} repeats BaoStock's"
    elif row["seed_close"] == reference:
        take, verdict = "seed", f"{stated} repeats the seed's"
    else:
        take, verdict = "baostock", f"{stated} repeats neither: {flagged}"
    return OHLC, take, f"{closes}; {verdict}{also}"


def _average(row: dict) -> tuple[tuple, str, str]:
    """The seed's amount (same bar) or volume and amount (other volume) when the seed bar passes the check; else null."""
    baostock = f"BaoStock volume {row['volume']} amount {row['amount']:.2f} (average {_average_price(row, '')})"
    span = f"[{_yuan(row['low'])}, {_yuan(row['high'])}]"
    if row["seed_volume"] is None:
        return ("amount",), "null", f"{baostock} outside {span} and no seed bar: amount null, bar kept"
    seed = f"seed volume {row['seed_volume']} amount {row['seed_amount']:.2f} (average {_average_price(row, 'seed_')})"
    same_prices = all(row[c] == row[f"seed_{c}"] for c in OHLC)
    if (row["seed_low"], row["seed_high"]) != (row["low"], row["high"]):
        span += f", seed [{_yuan(row['seed_low'])}, {_yuan(row['seed_high'])}]"
    stated = f"{baostock}, {seed} against {span}"
    if not row["seed_paid"]:
        return ("amount",), "null", f"{stated}: the seed bar fails the check too, amount null, bar kept"
    if not row["same_volume"]:
        return ("volume", "amount"), "seed", f"{stated}: the volumes differ and the seed bar passes"
    if same_prices:
        return ("amount",), "seed", f"{stated}: same bar, the seed amount passes"
    return ("amount",), "null", f"{stated}: same volume but other prices, amount null, bar kept"


def _average_price(row: dict, prefix: str) -> str:
    volume = row[f"{prefix}volume"]
    return f"{row[f'{prefix}amount'] / volume:.4f}" if volume else "none"


def _yuan(li: int | None) -> str:
    """厘 as yuan with two decimals, three when the value is not whole fen."""
    if li is None:
        return "none"
    return f"{li / 1000:.2f}" if li % 10 == 0 else f"{li / 1000:.3f}"
