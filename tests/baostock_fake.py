"""A fake baostock client that mimics its paging; responses come from recorded data."""

import functools
import json
from pathlib import Path
from types import SimpleNamespace

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


@functools.cache
def recording() -> dict:
    """tests/data/baostock.json, read on first use rather than at import."""
    return json.loads((Path(__file__).parent / "data" / "baostock.json").read_text("utf-8"))
