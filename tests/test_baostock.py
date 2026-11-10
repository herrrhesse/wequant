import datetime as dt
import socket
import threading

import polars as pl
import pytest

from tests.baostock_fake import FakeClient, FakeResult, recording
from wequant.data.schema import TABLES
from wequant.data.sources import baostock as bsmod
from wequant.data.sources.baostock import (
    PAGE_ROWS,
    BaoStock,
    BaoStockError,
    GuardedSocket,
    from_baostock,
    to_baostock,
)
from wequant.data.sources.base import CORPORATE_ACTIONS, DAILY, SECURITIES_ON


def recorded(name):
    case = recording()[name]
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
    assert dict(bars.schema) == DAILY
    raw = recording()["daily_traded"]["rows"][-1]
    f = recording()["daily_traded"]["fields"]
    last = bars.row(-1, named=True)
    assert last["date"] == dt.date.fromisoformat(raw[f.index("date")])
    assert last["close"] == round(float(raw[f.index("close")]) * 100) * 10
    assert last["preclose"] == round(float(raw[f.index("preclose")]) * 100) * 10
    assert last["volume"] == int(raw[f.index("volume")])
    assert last["amount"] == float(raw[f.index("amount")])
    assert last["tradestatus"] == 1 and last["is_st"] is False
    call = source.client.calls[-1]
    assert call[1]["code"] == "sh.600000" and call[1]["adjustflag"] == "3"


def test_reference_queries():
    source = session(
        query_stock_basic=[recorded("stock_basic")],
        query_all_stock=[recorded("all_stock")],
        query_trade_dates=[recorded("trade_dates")],
        query_adjust_factor=[recorded("adjust_factor")],
        query_dividend_data=[recorded("dividend"), FakeResult(recording()["dividend"]["fields"], [])],
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


NOT_LOGGED_IN = FakeResult([], [], "10001001", "not logged in")
RECV_FAILED = FakeResult([], [], "10002007", "recv")


@pytest.mark.parametrize(
    "answers, delays, logins",
    [
        ([NOT_LOGGED_IN, RECV_FAILED, "ok"], [1.0, 2.0], 2),  # a kicked session logs in again, then succeeds
        ([RECV_FAILED] * 4, [1.0, 2.0, 4.0], 1),  # three retries with backoff, then the error
    ],
    ids=["recovers", "gives_up"],
)
def test_retries_back_off_and_log_in_again(monkeypatch, answers, delays, logins):
    slept = []
    monkeypatch.setattr(bsmod.time, "sleep", slept.append)
    replies = [recorded("trade_dates") if answer == "ok" else answer for answer in answers]
    source = BaoStock(client=FakeClient(query_trade_dates=replies))
    source.login()
    if answers[-1] == "ok":
        assert source.query("query_trade_dates")[1]
    else:
        with pytest.raises(BaoStockError, match="10002007"):
            source.query("query_trade_dates")
    assert slept == delays and [c[0] for c in source.client.calls].count("login") == logins
    assert [e[1] for e in source.errors] == [a.error_code for a in answers if a != "ok"]


class StubSocket:
    def __init__(self, replies):
        self.replies, self.timeout = list(replies), None

    def settimeout(self, seconds):
        self.timeout = seconds

    def recv(self, size):
        return self.replies.pop(0)


def test_a_guarded_socket_raises_once_the_server_has_closed():
    sock = GuardedSocket(StubSocket([b"reply", b""]))
    assert sock.timeout == bsmod.REPLY_TIMEOUT
    assert sock.recv(8192) == b"reply"
    with pytest.raises(ConnectionError):
        sock.recv(8192)


def serve_once(handle) -> int:
    """The port of a local server that reads the first request, hands the connection to handle, then closes it."""
    server = socket.create_server(("127.0.0.1", 0))

    def run():
        with server, server.accept()[0] as conn:
            conn.recv(65536)  # an unread request would make the close a reset, which baostock survives
            handle()

    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1]


@pytest.fixture
def released():
    event = threading.Event()
    yield event
    event.set()


@pytest.mark.parametrize("silent", [False, True], ids=["closed", "silent"])
def test_a_dead_connection_fails_the_login_instead_of_hanging(silent, released, monkeypatch):
    constants = pytest.importorskip("baostock.common.contants")
    monkeypatch.setattr(bsmod, "REPLY_TIMEOUT", 0.5)
    monkeypatch.setattr(constants, "BAOSTOCK_SERVER_IP", "127.0.0.1")
    monkeypatch.setattr(constants, "BAOSTOCK_SERVER_PORT", serve_once(released.wait if silent else lambda: None))
    errors = []

    def login():
        try:
            BaoStock(retries=0).login()
        except BaoStockError as error:
            errors.append(error)

    thread = threading.Thread(target=login, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "the login hangs on a dead connection"
    assert [error.code for error in errors] == ["10002007"]
