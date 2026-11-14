"""A fake BaoStock that publishes a few days after a small seed store, for the daily update's tests. Prices in 厘."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from tests.baostock_fake import ADJUST_FACTOR, STOCK_BASIC, FakeResult
from tests.baostock_raw_fake import CALENDAR, DAY_COLUMNS, STEADY, World, day_row
from wequant.data.sources.baostock import INDEX_FIELDS, to_baostock
from wequant.data.store import Store

D4, D5 = dt.date(2024, 1, 5), dt.date(2024, 1, 8)
AFTER_CLOSE = {D4: dt.datetime(2024, 1, 5, 10, 0, tzinfo=dt.UTC), D5: dt.datetime(2024, 1, 8, 10, 0, tzinfo=dt.UTC)}
B_CLOSES = {day: 5000 + 100 * n for n, day in enumerate(CALENDAR)}  # 600001.SH: 50.00 .. 54.00 by D3
NAMES = {"600000.SH": "甲股份", "600001.SH": "乙股份"}


def seed_store(home: Path) -> Store:
    """Two stocks' seed bars on the calendar C1..D3, the CSI 300 on C1, no raw layer."""
    world = World()
    world.stock("600000.SH", STEADY, raw=())
    world.stock("600001.SH", B_CLOSES, raw=())
    store, _ = world.write(home)
    return store


def rows(day: dt.date, **changes: dict) -> list[dict]:
    """Both stocks' snapshot rows on D4 or D5: each closes 1.00 above the day before, changes by code."""
    n = (D4, D5).index(day) + 1
    a = day_row("600000.SH", day, 10400 + 100 * n, preclose=10400 + 100 * (n - 1))
    b = day_row("600001.SH", day, 5400 + 100 * n, preclose=5400 + 100 * (n - 1))
    return [a | changes.get("600000.SH", {}), b | changes.get("600001.SH", {})]


@dataclass
class Market:
    """A baostock client answering from its fields; fail_on names a method that loses the connection."""

    days: dict[dt.date, list[dict]] = field(default_factory=dict)  # published snapshots
    trading: tuple[dt.date, ...] = (*CALENDAR, D4, D5)
    events: dict[dt.date, list[tuple[str, float]]] = field(default_factory=dict)  # (code, back factor) by ex-date
    histories: dict[str, list[tuple[dt.date, float]]] = field(default_factory=dict)  # per-code factor histories
    members: dict[str, list[str] | Callable[[str], list[str]]] = field(default_factory=dict)  # by query method; a
    # callable gives the list on the day asked for
    names: dict[str, str] = field(default_factory=lambda: dict(NAMES))  # stock_basic's stocks
    listed: dt.date | None = None  # the member lists' date; None: the day asked for
    fail_on: str | None = None
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def login(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self):
        pass

    def __getattr__(self, method: str):
        if not method.startswith("query_"):
            raise AttributeError(method)

        def query(**params):
            self.calls.append((method, params))
            if method == self.fail_on:
                raise ConnectionError("BaoStock closed the connection")
            return getattr(self, f"_{method}")(**params)

        return query

    def _query_trade_dates(self, start_date, end_date):
        days = [d.isoformat() for d in self.trading if start_date <= d.isoformat() <= end_date]
        return FakeResult(["calendar_date", "is_trading_day"], [[day, "1"] for day in days])

    def _query_daily_history_k_AStock(self, date):
        rows = self.days.get(dt.date.fromisoformat(date), [])
        return FakeResult(list(DAY_COLUMNS), [[row[c] for c in DAY_COLUMNS] for row in rows])

    def _query_daily_adjust_factor(self, date):
        found = self.events.get(dt.date.fromisoformat(date), [])
        return FakeResult(ADJUST_FACTOR, [[to_baostock(c), date, "1.000000", f"{f:.6f}", f"{f:.6f}"] for c, f in found])

    def _query_adjust_factor(self, code, start_date, end_date):
        wequant_code = f"{code[3:]}.{code[:2].upper()}"
        history = self.histories.get(wequant_code, [])
        return FakeResult(ADJUST_FACTOR, [[code, d.isoformat(), "", f"{f:.6f}", f"{f:.6f}"] for d, f in history])

    def _query_stock_basic(self):
        rows = [[to_baostock(code), name, "2023-01-03", "", "1", "1"] for code, name in self.names.items()]
        return FakeResult(STOCK_BASIC, rows)

    def _query_history_k_data_plus(self, code, fields, start_date, end_date, frequency, adjustflag):
        if fields != INDEX_FIELDS:  # one stock's history (round 3's build): its snapshot rows over the window
            names, days = fields.split(","), sorted(d for d in self.days if start_date <= d.isoformat() <= end_date)
            rows = [[row[f] for f in names] for d in days for row in self.days[d] if row["code"] == code]
            return FakeResult(names, rows)
        days = [d for d in self.days if start_date <= d.isoformat() <= end_date]
        return FakeResult(
            fields.split(","), [[d.isoformat(), code, *["3100.0000"] * 4, "1000000", "3.1e9"] for d in days]
        )

    def _query_hs300_stocks(self, date):
        return self._members("query_hs300_stocks", date)

    def _query_zz500_stocks(self, date):
        return self._members("query_zz500_stocks", date)

    def _members(self, method, date):
        listed = self.listed.isoformat() if self.listed else date
        found = self.members.get(method, [])
        codes = found(date) if callable(found) else found
        rows = [[listed, to_baostock(code), self.names.get(code, "")] for code in codes]
        return FakeResult(["updateDate", "code", "code_name"], rows)
