"""A fake baostock client that mimics its paging, and synthetic replies in BaoStock's field formats."""

import socket
import sys
from types import ModuleType, SimpleNamespace

from wequant.data.sources.baostock import PAGE_ROWS


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


STOCK_BASIC = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
SNAPSHOT = ["date", "code", "open", "high", "low", "close", "preclose", "volume", "amount", "adjustflag", "turn"]
SNAPSHOT += ["tradestatus", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"]
ADJUST_FACTOR = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]


def stock_basic() -> tuple[list[str], list[list[str]]]:
    """(fields, rows): four stocks (one delisted) and an index, as query_stock_basic lists them."""
    rows = [
        ["sh.600000", "股票甲", "2000-01-04", "", "1", "1"],
        ["sh.600001", "股票乙", "2001-01-04", "", "1", "1"],
        ["sz.300001", "股票丙", "2009-10-30", "", "1", "1"],
        ["sz.000002", "股票丁", "2002-01-04", "2020-06-30", "1", "0"],
        ["sh.000001", "指数甲", "1991-07-15", "", "2", "1"],
    ]
    return STOCK_BASIC, rows


def daily_snapshot(day: str) -> tuple[list[str], list[list[str]]]:
    """(fields, rows): three stocks on one day as query_daily_history_k_AStock returns them, the last suspended."""
    rows = [
        [day, "sh.600000", "10.0000", "10.2000", "9.9000", "10.1000", "10.0000", "100000", "1010000.0000", "3"]
        + ["0.100000", "1", "1.000000", "10.000000", "1.000000", "1.000000", "10.000000", "0"],
        [day, "sh.600001", "5.0000", "5.0500", "4.9500", "5.0000", "5.0000", "200000", "1000000.0000", "3"]
        + ["0.200000", "1", "0.000000", "20.000000", "2.000000", "2.000000", "20.000000", "1"],
        [day, "sz.300001", "", "", "", "", "20.0000", "", "", "3", "", "0", "", "", "", "", "", "0"],
    ]
    return SNAPSHOT, rows


def adjust_factor(code: str) -> tuple[list[str], list[list[str]]]:
    """(fields, rows): three adjustment events of one code, as query_adjust_factor returns them."""
    rows = [
        [code, "2001-06-01", "0.500000", "1.000000", "1.000000"],
        [code, "2002-06-03", "0.625000", "1.250000", "1.250000"],
        [code, "2003-06-02", "0.750000", "1.500000", "1.500000"],
    ]
    return ADJUST_FACTOR, rows


def install_baostock(monkeypatch, host: str, port: int) -> None:
    """Put a stand-in for the baostock package in sys.modules, pointed at host:port: the modules the adapter patches,
    with baostock 0.9.4's own connect, its reply loop (recv until the end marker, any exception -> no reply) and its
    login answer to no reply, error 10002007."""
    package, util, common = ModuleType("baostock"), ModuleType("baostock.util"), ModuleType("baostock.common")
    socketutil, context = ModuleType("baostock.util.socketutil"), ModuleType("baostock.common.context")

    class SocketUtil:
        def connect(self, api_key):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            context.default_socket = sock

    def send_msg(msg: str) -> str | None:
        try:
            sock = context.default_socket
            sock.send(bytes(msg + "\n", encoding="utf-8"))
            receive = b""
            while True:
                receive += sock.recv(8192)
                if receive[-13:] == b"<![CDATA[]]>\n":
                    return bytes.decode(receive)
        except Exception:
            return None

    def login():
        SocketUtil().connect("0")
        if not send_msg("login"):
            return SimpleNamespace(error_code="10002007", error_msg="网络接收错误。")
        return SimpleNamespace(error_code="0", error_msg="success")

    socketutil.SocketUtil, socketutil.send_msg = SocketUtil, send_msg
    package.login, package.logout = login, lambda: None
    modules = {"baostock": package, "baostock.util": util, "baostock.common": common}
    modules |= {"baostock.util.socketutil": socketutil, "baostock.common.context": context}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
