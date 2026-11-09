import datetime as dt

import polars as pl
import pytest

from tests.baostock_raw_fake import D1, D2, MERGED, SOURCE, World, day_row, suspended_row
from wequant.data.merge import RawDataError, plan_merge
from wequant.data.merge.daily import read_daily, split
from wequant.data.schema import BARS, DAILY_STATUS
from wequant.data.sources.baostock import parse_daily
from wequant.data.sources.base import DAILY

STEADY = {D1: 10000, D2: 10100, dt.date(2024, 1, 4): 10200}
CHEAP = dict.fromkeys(("open", "high", "low", "close"), "0.5000")


def parsed(*rows: dict[str, str]) -> pl.DataFrame:
    return parse_daily(pl.DataFrame(list(rows))).with_columns(source=pl.lit(SOURCE))


def test_parse_daily_golden():
    row = day_row("600000.SH", D1, 10030, preclose=9870, volume=64270729, amount="640470012.0700")
    row |= {"open": "9.9100", "high": "10.0700", "low": "9.8400"}
    daily = parse_daily(pl.DataFrame([row]))
    assert dict(daily.schema) == DAILY
    assert daily.row(0, named=True) == {
        "code": "600000.SH",
        "date": D1,
        "open": 9910,
        "high": 10070,
        "low": 9840,
        "close": 10030,
        "preclose": 9870,
        "volume": 64270729,
        "amount": 640470012.07,
        "tradestatus": 1,
        "is_st": False,
    }


def test_parse_daily_empty_fields_are_null():
    halted = parse_daily(pl.DataFrame([day_row("600000.SH", D1, 10030, volume="", amount="", tradestatus="0")]))
    assert halted.row(0, named=True)["volume"] is None and halted.row(0, named=True)["amount"] is None
    empty = parse_daily(pl.DataFrame([suspended_row("600000.SH", D1)])).row(0, named=True)
    assert all(empty[p] is None for p in ("open", "high", "low", "close", "preclose"))
    assert empty["tradestatus"] == 0 and empty["is_st"] is False


def test_split_suspended_traded_while_suspended_and_no_volume():
    daily = parsed(
        day_row("600000.SH", D1, 10030, volume="", amount="", tradestatus="0"),
        day_row("600647.SH", D1, 1790, preclose=1640, volume=2621826, tradestatus="0", open=1630, high=1800, low=1630),
        day_row("600004.SH", D1, 8220, volume="0", amount="0.0000"),
    )
    bars, status = split(daily)
    assert dict(bars.schema) == BARS.schema and dict(status.schema) == DAILY_STATUS.schema
    bar = bars.row(0, named=True)
    assert bars.height == 1 and bar["code"] == "600647.SH" and bar["preclose_derived"] is False
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["preclose"]) == (1630, 1800, 1630, 1790, 1640)
    assert (bar["tradestatus"], bar["is_st"], bar["source"]) == (0, False, SOURCE)
    assert status.select("code", "tradestatus").rows() == [("600000.SH", 0), ("600647.SH", 0), ("600004.SH", 1)]


def test_read_daily_adds_the_source(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY, seed=())
    _, raw = w.write(tmp_path)
    daily = read_daily(raw, MERGED)
    assert daily.columns == [*DAILY, "source"] and daily["source"].unique().to_list() == [SOURCE]
    assert daily["close"].to_list() == [10000, 10100, 10200]


BROKEN = [
    ("price", lambda rows: rows[0].update(close="9.9150")),
    ("tradestatus", lambda rows: rows[0].update(tradestatus="2")),
    ("isST", lambda rows: rows[0].update(isST="")),
    ("unique", lambda rows: rows.append(dict(rows[0]))),
    ("date", lambda rows: rows[0].update(date=D2.isoformat())),
    ("traded", lambda rows: rows[0].update(high="10.0000", close="10.1000")),
    ("traded", lambda rows: rows[0].update(open="9.9000", low="9.9500")),
    ("traded", lambda rows: rows[0].update(preclose="")),
    ("code", lambda rows: rows[0].update(code="bj.920001")),
    ("code", lambda rows: rows[0].update(code="sh.000001")),
    ("volume", lambda rows: rows[0].update(volume="1000.0")),
    ("amount", lambda rows: rows[0].update(amount="1.5e8")),
    ("average_price", lambda rows: rows[0].update(amount="0.0000")),
    ("average_price", lambda rows: rows[0].update(amount="1.0000")),
    ("average_price", lambda rows: rows[0].update(amount="10020.0000")),
    ("average_price", lambda rows: rows[0].update(tradestatus="0", volume="1118342", amount="0.0000")),
    ("average_price", lambda rows: rows[0].update(CHEAP, volume="1", amount="0.0000")),  # worth half a yuan
]


def test_amounts_cut_to_the_whole_yuan_pass(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    w.days[D1][0].update(CHEAP, volume="113", amount="56.0000")  # 113 shares at 0.50 are 56.50 yuan
    store, raw = w.write(tmp_path)
    assert read_daily(raw, [D1])["amount"].to_list() == [56.0]


@pytest.mark.parametrize("check, change", BROKEN)
def test_broken_raw_rows_raise_naming_the_check(tmp_path, check, change):
    w = World()
    w.stock("600000.SH", STEADY)
    change(w.days[D1])
    store, raw = w.write(tmp_path)
    with pytest.raises(RawDataError) as error:
        plan_merge(store, raw)
    assert len(error.value.problems) == 1 and error.value.problems[0].startswith(f"{check}: ")
    assert store.manifest()["version"] == 1
