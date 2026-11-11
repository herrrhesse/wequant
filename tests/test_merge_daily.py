import datetime as dt

import polars as pl
import pytest

from tests.baostock_raw_fake import D1, D2, SOURCE, World, day_row, empty_resolutions, suspended_row
from wequant.data.merge import RawDataError, plan_merge
from wequant.data.merge.daily import paid, split
from wequant.data.schema import BARS, DAILY_STATUS
from wequant.data.sources.baostock import parse_daily
from wequant.data.sources.base import DAILY

STEADY = {D1: 10000, D2: 10100, dt.date(2024, 1, 4): 10200}


def parsed(*rows: dict[str, str]) -> pl.DataFrame:
    return parse_daily(pl.DataFrame(list(rows)), turn=True).with_columns(source=pl.lit(SOURCE))


def test_parse_daily_golden():
    row = day_row("600000.SH", D1, 10100, preclose=10000, volume=1234567, amount="12469126.7800")
    row |= {"open": "10.0500", "high": "10.2300", "low": "9.9800"}
    daily = parse_daily(pl.DataFrame([row]))
    assert dict(daily.schema) == DAILY
    assert daily.row(0, named=True) == {
        "code": "600000.SH",
        "date": D1,
        "open": 10050,
        "high": 10230,
        "low": 9980,
        "close": 10100,
        "preclose": 10000,
        "volume": 1234567,
        "amount": 12469126.78,
        "tradestatus": 1,
        "is_st": False,
    }
    with_turn = parse_daily(pl.DataFrame([row | {"turn": "1.250000"}]), turn=True)
    assert with_turn.columns == [*DAILY, "turn"] and with_turn["turn"].item() == 1.25


def test_an_exchange_preclose_keeps_its_li():
    """D11: an exchange preclose after an event may be whole 厘 (5.968); prices are whole fen."""
    row = day_row("000010.SZ", D1, 7000, volume=100) | {"preclose": "5.9680", "open": "7.0040"}
    daily = parse_daily(pl.DataFrame([row])).row(0, named=True)
    assert (daily["preclose"], daily["open"]) == (5968, 7000)


def test_parse_daily_empty_fields_are_null():
    halted = parse_daily(pl.DataFrame([day_row("600000.SH", D1, 10030, volume="", amount="", tradestatus="0")]))
    assert halted.row(0, named=True)["volume"] is None and halted.row(0, named=True)["amount"] is None
    empty = parse_daily(pl.DataFrame([suspended_row("600000.SH", D1)])).row(0, named=True)
    assert all(empty[p] is None for p in ("open", "high", "low", "close", "preclose"))
    assert empty["tradestatus"] == 0 and empty["is_st"] is False
    assert parse_daily(pl.DataFrame([suspended_row("600000.SH", D1)]), turn=True)["turn"].item() is None


def test_split_suspended_traded_while_suspended_and_no_volume():
    daily = parsed(
        day_row("600000.SH", D1, 10030, volume="", amount="", tradestatus="0"),
        day_row(
            "600647.SH", D1, 10500, preclose=10000, volume=300000, tradestatus="0", open=10100, high=10600, low=10100
        ),
        day_row("600004.SH", D1, 8220, volume="0", amount="0.0000"),
    )
    bars, status = split(daily)
    assert dict(bars.schema) == BARS.schema and dict(status.schema) == DAILY_STATUS.schema
    bar = bars.row(0, named=True)
    assert bars.height == 1 and bar["code"] == "600647.SH" and bar["preclose_derived"] is False
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["preclose"]) == (10100, 10600, 10100, 10500, 10000)
    assert (bar["tradestatus"], bar["is_st"], bar["source"]) == (0, False, SOURCE)
    assert status.select("code", "tradestatus").rows() == [("600000.SH", 0), ("600647.SH", 0), ("600004.SH", 1)]


BROKEN = [
    ("price", lambda rows: rows[0].update(close="9.9150")),
    ("preclose", lambda rows: rows[0].update(preclose="9.9155")),
    ("tradestatus", lambda rows: rows[0].update(tradestatus="2")),
    ("isST", lambda rows: rows[0].update(isST="")),
    ("unique", lambda rows: rows.append(dict(rows[0]))),
    ("date", lambda rows: rows[0].update(date=D2.isoformat())),
    ("traded", lambda rows: rows[0].update(high="10.0000", close="10.1000")),
    ("code", lambda rows: rows[0].update(code="bj.920001")),
    ("volume", lambda rows: rows[0].update(volume="1000.0")),
    ("amount", lambda rows: rows[0].update(amount="1.5e8")),
    ("turn", lambda rows: rows[0].update(turn="1.2e3")),
]
PAID = {  # low, high (厘), volume, amount -> whether paid() passes the amount
    "above_high": (10000, 10000, 1000, 10020.0, False),  # 20 yuan over 10,000: beyond 1e-3 and one yuan
    "no_amount_with_volume": (10000, 10000, 5000, 0.0, False),  # a suspended row with volume and no amount (D9)
    "no_amount_under_a_yuan": (500, 500, 1, 0.0, False),  # 0.50 yuan traded: inside the yuan tolerance, still unpaid
    "cut_to_the_yuan": (500, 500, 113, 56.0, True),  # 113 shares at 0.50 are 56.50 yuan, cut to 56
}


@pytest.mark.parametrize("low, high, volume, amount, passes", PAID.values(), ids=PAID)
def test_paid_holds_amounts_to_low_high(low, high, volume, amount, passes):
    schema = {"low": pl.Int32, "high": pl.Int32, "volume": pl.Int64, "amount": pl.Float64}
    bar = pl.DataFrame({"low": [low], "high": [high], "volume": [volume], "amount": [amount]}, schema=schema)
    assert bar.select(paid()).item() is passes


@pytest.mark.parametrize("check, change", BROKEN)
def test_broken_raw_rows_raise_naming_the_check(tmp_path, check, change):
    w = World()
    w.stock("600000.SH", STEADY)
    change(w.days[D1])
    store, raw = w.write(tmp_path)
    with pytest.raises(RawDataError) as error:
        plan_merge(store, raw, resolutions=empty_resolutions(tmp_path))
    assert len(error.value.problems) == 1 and error.value.problems[0].startswith(f"{check}: ")
    assert store.manifest()["version"] == 1
