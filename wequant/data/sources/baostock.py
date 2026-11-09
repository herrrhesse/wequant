"""BaoStock adapter (optional extra: wequant[baostock]).

Rows are read with rs.next() / get_row_data(), never through pandas. Codes become 600000.SH,
prices int32 厘, volume shares, amount yuan, and empty strings null.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib
import io
import time

import polars as pl

from wequant.data import codes as codemod
from wequant.data.schema import TABLES
from wequant.data.sources.base import CORPORATE_ACTIONS, DAILY, SECURITIES_ON

SOURCE = "baostock"
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"
PAGE_ROWS = 2000  # BaoStock pages results; a full page means next() fetches another one
FIRST_DAY = "1990-12-19"  # first trading day in China; BaoStock's own defaults start in 2015
NOT_LOGGED_IN = "10001001"


class BaoStockError(RuntimeError):
    def __init__(self, method: str, code: str, message: str):
        super().__init__(f"{method}: {code} {message}")
        self.code = code


def to_baostock(code: str) -> str:
    """600000.SH -> sh.600000. BaoStock has no Beijing Stock Exchange data and rejects bj codes."""
    digits, exchange = codemod.split(code)
    if exchange == "BJ":
        raise ValueError(f"BaoStock does not cover the Beijing Stock Exchange: {code}")
    return f"{exchange.lower()}.{digits}"


def from_baostock(code: str) -> str:
    exchange, digits = code.split(".")
    return f"{digits}.{exchange.upper()}"


class BaoStock:
    """A BaoStock session; use it as `with BaoStock() as source: ...`.

    Queries are retried with exponential backoff (at most `retries` times). `requests` counts
    every round trip to the server, logins and extra pages included.
    """

    def __init__(self, client=None, retries: int = 3, backoff: float = 1.0):
        self.client = client
        self.retries = retries
        self.backoff = backoff
        self.requests = 0
        self.errors: list[tuple[str, str, str]] = []  # (method, error code, message) of every failure, retried or not

    def __enter__(self) -> BaoStock:
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        self.logout()

    def login(self) -> None:
        if self.client is None:
            self.client = importlib.import_module("baostock")
        result = _quiet(self.client.login)
        self.requests += 1
        if result.error_code != "0":
            raise BaoStockError("login", result.error_code, result.error_msg)

    def logout(self) -> None:
        _quiet(self.client.logout)
        self.requests += 1

    def query(self, method: str, **params) -> tuple[list[str], list[list[str]]]:
        """(fields, rows) of one query with every page read."""
        attempt = 0
        while True:
            try:
                return self._fetch(method, params)
            except BaoStockError as error:
                self.errors.append((method, error.code, str(error)))
                if attempt == self.retries:
                    raise
                time.sleep(self.backoff * 2**attempt)
                attempt += 1
                if error.code == NOT_LOGGED_IN:
                    self.login()

    def raw(self, method: str, **params) -> pl.DataFrame:
        """One query's response as returned: every field a string column."""
        return _frame(*self.query(method, **params))

    def daily_snapshot(self, day: dt.date | str) -> pl.DataFrame:
        """Every SH/SZ stock's daily row for one day, suspended stocks included, as raw strings."""
        return self.raw("query_daily_history_k_AStock", date=str(day))

    # ------------------------------------------------------------ DataSource

    def securities(self) -> pl.DataFrame:
        stocks = _frame(*self.query("query_stock_basic")).filter(pl.col("type") == "1")
        codes = stocks.select(_code("code")).to_series().to_list()
        return stocks.select(
            code=_code("code"),
            exchange=pl.Series([codemod.split(c)[1] for c in codes]),
            board=pl.Series([_board(c) for c in codes], dtype=pl.String),
            first_date=_text("ipoDate").str.to_date(),
            last_date=_text("outDate").str.to_date(),
            name=_text("code_name"),
            source=pl.lit(SOURCE),
        )

    def calendar(self, start: dt.date | str, end: dt.date | str) -> pl.DataFrame:
        days = _frame(*self.query("query_trade_dates", start_date=str(start), end_date=str(end)))
        return days.filter(pl.col("is_trading_day") == "1").select(date=pl.col("calendar_date").str.to_date())

    def securities_on(self, day: dt.date | str) -> pl.DataFrame:
        listed = _frame(*self.query("query_all_stock", day=str(day)))
        return listed.select(
            code=_code("code"),
            date=pl.lit(str(day)).str.to_date(),
            name=_text("code_name"),
            tradestatus=_text("tradeStatus").cast(pl.Int8),
        ).select(list(SECURITIES_ON))

    def daily_bars(self, code: str, start: dt.date | str, end: dt.date | str) -> pl.DataFrame:
        fields, rows = self.query(
            "query_history_k_data_plus",
            code=to_baostock(code),
            fields=DAILY_FIELDS,
            start_date=str(start),
            end_date=str(end),
            frequency="d",
            adjustflag="3",
        )
        decimals = 2 if codemod.security_type(code) == "stock" else 3
        return (
            _frame(fields, rows)
            .select(
                code=pl.lit(code),
                date=pl.col("date").str.to_date(),
                **{f: _li(f, decimals) for f in ("open", "high", "low", "close")},
                volume=_text("volume").cast(pl.Float64).round(0).cast(pl.Int64),
                amount=_text("amount").cast(pl.Float64),
                preclose=_li("preclose", decimals),
                tradestatus=_text("tradestatus").cast(pl.Int8),
                is_st=_text("isST").cast(pl.Int8).cast(pl.Boolean),
            )
            .select(list(DAILY))
        )

    def adj_factors(self, code: str) -> pl.DataFrame:
        factors = _frame(
            *self.query(
                "query_adjust_factor",
                code=to_baostock(code),
                start_date=FIRST_DAY,
                end_date=dt.date.today().isoformat(),
            )
        )
        return factors.select(
            code=pl.lit(code),
            ex_date=pl.col("dividOperateDate").str.to_date(),
            factor=_text("backAdjustFactor").cast(pl.Float64),
        ).select(list(TABLES["adj_factor"].schema))

    def corporate_actions(self, code: str, start_year: int, end_year: int) -> pl.DataFrame:
        frames = [
            _frame(*self.query("query_dividend_data", code=to_baostock(code), year=str(year), yearType="operate"))
            for year in range(start_year, end_year + 1)
        ]
        frames = [f for f in frames if f.height]
        if not frames:
            return pl.DataFrame(schema=CORPORATE_ACTIONS)
        return pl.concat(frames).select(
            code=pl.lit(code),
            ex_date=_text("dividOperateDate").str.to_date(),
            record_date=_text("dividRegistDate").str.to_date(),
            pay_date=_text("dividPayDate").str.to_date(),
            cash_before_tax=_text("dividCashPsBeforeTax").cast(pl.Float64),
            bonus_shares=_text("dividStocksPs").cast(pl.Float64),
            transfer_shares=_text("dividReserveToStockPs").cast(pl.Float64),
            plan=_text("dividCashStock"),
        )

    # ------------------------------------------------------------ internals

    def _fetch(self, method: str, params: dict) -> tuple[list[str], list[list[str]]]:
        result = getattr(self.client, method)(**params)
        self.requests += 1
        if result.error_code != "0":
            raise BaoStockError(method, result.error_code, result.error_msg)
        rows = []
        while True:
            if result.cur_row_num == len(result.data) == PAGE_ROWS:
                self.requests += 1  # next() is about to fetch another page
            if not result.next():
                break
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise BaoStockError(method, result.error_code, result.error_msg)
        if len(result.data) == PAGE_ROWS:  # a page fetch failed without an error code
            raise BaoStockError(method, "truncated", f"stopped after {len(rows)} rows")
        return list(result.fields), rows


def _quiet(call):
    with contextlib.redirect_stdout(io.StringIO()):
        return call()


def _frame(fields: list[str], rows: list[list[str]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={f: pl.String for f in fields}, orient="row")


def _text(column: str) -> pl.Expr:
    return pl.when(pl.col(column) != "").then(pl.col(column))


def _code(column: str) -> pl.Expr:
    return pl.col(column).str.slice(3) + "." + pl.col(column).str.slice(0, 2).str.to_uppercase()


def _li(column: str, decimals: int) -> pl.Expr:
    """Price text rounded to `decimals` places, in 0.001 units (int32)."""
    return ((_text(column).cast(pl.Float64) * 10**decimals).round(0) * 10 ** (3 - decimals)).cast(pl.Int32)


def _board(code: str) -> str | None:
    try:
        return codemod.board(code)
    except ValueError:
        return None
