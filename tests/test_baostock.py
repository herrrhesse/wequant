import datetime as dt
import socket
import threading

import pytest

from tests.baostock_fake import FakeClient, FakeResult, install_baostock, stock_basic
from wequant.data.schema import TABLES
from wequant.data.sources import baostock as bsmod
from wequant.data.sources.baostock import (
    DAILY_FIELDS,
    PAGE_ROWS,
    BaoStock,
    BaoStockError,
    GuardedSocket,
    from_baostock,
    to_baostock,
)
from wequant.data.sources.base import DAILY


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
    """Prices int32 厘, volume int64 shares, amount float64 yuan, empty fields null; unadjusted prices requested."""
    rows = [
        ["2024-01-02", "sh.600000", "10.0500", "10.2300", "9.9800", "10.1000", "10.0000", "1234567", "12469126.7800"]
        + ["1", "0"],
        ["2024-01-03", "sh.600000", "", "", "", "", "10.1000", "", "", "0", "1"],
    ]
    source = session(query_history_k_data_plus=[FakeResult(DAILY_FIELDS.split(","), rows)])
    bars = source.daily_bars("600000.SH", "2024-01-02", "2024-01-03")
    assert dict(bars.schema) == DAILY
    assert bars.rows() == [
        ("600000.SH", dt.date(2024, 1, 2), 10050, 10230, 9980, 10100, 10000, 1234567, 12469126.78, 1, False),
        ("600000.SH", dt.date(2024, 1, 3), None, None, None, None, 10100, None, None, 0, True),
    ]
    call = source.client.calls[-1]
    assert call[1]["code"] == "sh.600000" and call[1]["adjustflag"] == "3"


def test_securities_are_the_stocks_with_their_listing_dates():
    securities = session(query_stock_basic=[FakeResult(*stock_basic())]).securities()  # the index is left out
    assert dict(securities.schema) == TABLES["securities"].schema
    assert securities.select("code", "exchange", "board", "first_date", "last_date").rows() == [
        ("600000.SH", "SH", "main", dt.date(2000, 1, 4), None),
        ("600001.SH", "SH", "main", dt.date(2001, 1, 4), None),
        ("300001.SZ", "SZ", "chinext", dt.date(2009, 10, 30), None),
        ("000002.SZ", "SZ", "main", dt.date(2002, 1, 4), dt.date(2020, 6, 30)),
    ]


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
    ok = FakeResult(["calendar_date", "is_trading_day"], [["2024-01-02", "1"]])
    replies = [ok if answer == "ok" else answer for answer in answers]
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
    """Without the guard, baostock's reply loop spins on a closed connection and blocks on a silent one."""
    monkeypatch.setattr(bsmod, "REPLY_TIMEOUT", 0.5)
    install_baostock(monkeypatch, "127.0.0.1", serve_once(released.wait if silent else lambda: None))
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
