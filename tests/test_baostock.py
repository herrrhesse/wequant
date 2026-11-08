import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from wequant.data.schema import TABLES
from wequant.data.sources import baostock as bsmod
from wequant.data.sources.baostock import PAGE_ROWS, BaoStock, BaoStockError, from_baostock, to_baostock
from wequant.data.sources.base import CORPORATE_ACTIONS, SECURITIES_ON

RECORDED = json.loads((Path(__file__).parent / "data" / "baostock.json").read_text("utf-8"))


class FakeResult:
    """Mimics baostock's ResultData paging: a full page makes next() fetch the following one."""

    def __init__(self, fields, rows, error_code="0", error_msg="success", lose_page=None):
        self.fields, self.error_code, self.error_msg = fields, error_code, error_msg
        self._pages = [rows[i : i + PAGE_ROWS] for i in range(0, len(rows), PAGE_ROWS)] or [[]]
        self._lose_page = lose_page
        self.data, self.cur_row_num, self.cur_page_num = self._pages[0], 0, "1"

    def next(self):
        if self.cur_row_num < len(self.data):
            return True
        if len(self.data) < PAGE_ROWS:
            return False
        page = int(self.cur_page_num)
        if page == self._lose_page:
            return False  # the network read came back empty: no error code, data unchanged
        self.data = self._pages[page] if page < len(self._pages) else []
        self.cur_page_num, self.cur_row_num = str(page + 1), 0
        return bool(self.data)

    def get_row_data(self):
        row = self.data[self.cur_row_num]
        self.cur_row_num += 1
        return row


class FakeClient:
    def __init__(self, **responses):
        self.responses = {method: list(results) for method, results in responses.items()}
        self.calls = []

    def login(self):
        self.calls.append(("login", {}))
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self):
        self.calls.append(("logout", {}))

    def __getattr__(self, method):
        def query(**params):
            self.calls.append((method, params))
            return self.responses[method].pop(0)

        return query


def recorded(name):
    case = RECORDED[name]
    return FakeResult(case["fields"], case["rows"])


def session(**responses):
    source = BaoStock(client=FakeClient(**responses), backoff=0)
    source.login()
    return source


def test_codes_round_trip():
    assert to_baostock("600000.SH") == "sh.600000"
    assert from_baostock("sz.000001") == "000001.SZ"
    with pytest.raises(ValueError, match="Beijing"):
        to_baostock("920047.BJ")


def test_daily_bars_are_in_wequant_units():
    source = session(query_history_k_data_plus=[recorded("daily_traded")])
    bars = source.daily_bars("600000.SH", "2026-11-02", "2026-11-06")
    assert dict(bars.schema) == TABLES["bars"].schema
    raw = RECORDED["daily_traded"]["rows"][-1]
    f = RECORDED["daily_traded"]["fields"]
    last = bars.row(-1, named=True)
    assert last["date"] == dt.date.fromisoformat(raw[f.index("date")])
    assert last["close"] == round(float(raw[f.index("close")]) * 100) * 10
    assert last["preclose"] == round(float(raw[f.index("preclose")]) * 100) * 10
    assert last["volume"] == int(raw[f.index("volume")])
    assert last["amount"] == float(raw[f.index("amount")])
    assert last["tradestatus"] == 1 and last["is_st"] is False and last["preclose_derived"] is False
    assert set(bars["source"]) == {"baostock"}
    call = source.client.calls[-1]
    assert call[1]["code"] == "sh.600000" and call[1]["adjustflag"] == "3"


def test_suspended_days_keep_their_status_and_empty_fields_become_null():
    source = session(query_history_k_data_plus=[recorded("daily_suspended")])
    case = RECORDED["daily_suspended"]
    bars = source.daily_bars(case["code"], case["start"], case["end"])
    halted = bars.filter(pl.col("tradestatus") == 0)
    assert halted.height > 0
    raw_volumes = [
        r[case["fields"].index("volume")] for r in case["rows"] if r[case["fields"].index("tradestatus")] == "0"
    ]
    assert halted["volume"].null_count() == sum(v == "" for v in raw_volumes)


def test_st_flag():
    source = session(query_history_k_data_plus=[recorded("daily_st")])
    case = RECORDED["daily_st"]
    bars = source.daily_bars(case["code"], case["start"], case["end"])
    flags = [r[case["fields"].index("isST")] == "1" for r in case["rows"]]
    assert bars["is_st"].to_list() == flags and any(flags) and not all(flags)


def test_reference_queries():
    source = session(
        query_stock_basic=[recorded("stock_basic")],
        query_all_stock=[recorded("all_stock")],
        query_trade_dates=[recorded("trade_dates")],
        query_adjust_factor=[recorded("adjust_factor")],
        query_dividend_data=[recorded("dividend"), FakeResult(RECORDED["dividend"]["fields"], [])],
    )
    securities = source.securities()
    assert dict(securities.schema) == TABLES["securities"].schema
    assert securities.filter(pl.col("last_date").is_not_null()).height >= 1  # a delisted stock
    listed = source.securities_on("2026-11-06")
    assert dict(listed.schema) == SECURITIES_ON and listed["code"].str.contains(r"^\d{6}\.(SH|SZ|BJ)$").all()
    calendar = source.calendar("2026-10-26", "2026-11-08")
    assert dict(calendar.schema) == TABLES["calendar"].schema
    assert all(d.weekday() < 5 for d in calendar["date"])
    factors = source.adj_factors("600519.SH")
    assert dict(factors.schema) == TABLES["adj_factor"].schema and factors["factor"].is_sorted()
    actions = source.corporate_actions("600519.SH", 2025, 2026)
    assert dict(actions.schema) == CORPORATE_ACTIONS and actions.height >= 1
    assert actions["cash_before_tax"].drop_nulls().min() > 0


def test_pages_are_read_and_counted():
    rows = [[f"2020-01-{i:05d}"] for i in range(4500)]
    source = session(query_trade_dates=[FakeResult(["calendar_date"], rows)])
    before = source.requests
    _, got = source.query("query_trade_dates")
    assert len(got) == 4500 and source.requests - before == 3  # pages of 2000, 2000, 500
    full = [[str(i)] for i in range(2 * PAGE_ROWS)]
    source = session(query_trade_dates=[FakeResult(["calendar_date"], full)])
    before = source.requests
    assert len(source.query("query_trade_dates")[1]) == 4000
    assert source.requests - before == 3  # the empty third page is a request too


def test_a_silently_lost_page_raises():
    rows = [[str(i)] for i in range(3000)]
    source = session(query_trade_dates=[FakeResult(["calendar_date"], rows, lose_page=1)] * 4)
    with pytest.raises(BaoStockError, match="truncated"):
        source.query("query_trade_dates")


def test_retries_back_off_and_log_in_again(monkeypatch):
    delays = []
    monkeypatch.setattr(bsmod.time, "sleep", delays.append)
    ok = recorded("trade_dates")
    source = BaoStock(
        client=FakeClient(
            query_trade_dates=[
                FakeResult([], [], "10001001", "not logged in"),
                FakeResult([], [], "10002007", "recv"),
                ok,
            ]
        )
    )
    source.login()
    fields, rows = source.query("query_trade_dates")
    assert rows and delays == [1.0, 2.0]
    assert [c[0] for c in source.client.calls].count("login") == 2
    assert [e[1] for e in source.errors] == ["10001001", "10002007"]


def test_gives_up_after_three_retries(monkeypatch):
    delays = []
    monkeypatch.setattr(bsmod.time, "sleep", delays.append)
    failing = [FakeResult([], [], "10002007", "recv") for _ in range(4)]
    source = BaoStock(client=FakeClient(query_trade_dates=failing))
    source.login()
    with pytest.raises(BaoStockError, match="10002007"):
        source.query("query_trade_dates")
    assert delays == [1.0, 2.0, 4.0] and len(source.errors) == 4


@pytest.mark.network
def test_live_daily_bars():
    with BaoStock() as source:
        bars = source.daily_bars("600000.SH", "2026-11-02", "2026-11-06")
    assert dict(bars.schema) == TABLES["bars"].schema and bars.height == 5
