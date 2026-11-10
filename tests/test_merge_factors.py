import datetime as dt

import polars as pl
import pytest

from tests.baostock_raw_fake import D1, D2, D3, write_factors
from tests.frames import table_frame
from wequant.data.merge import RawDataError
from wequant.data.merge.factors import events, normalize, read_factors
from wequant.data.raw import BaoStockRaw
from wequant.data.sources.baostock import parse_adjust_factor

d = dt.date.fromisoformat


def adj(code: str, rows: list[tuple[str | dt.date, float]]) -> pl.DataFrame:
    return table_frame("adj_factor", [{"code": code, "ex_date": e, "factor": f} for e, f in rows])


def spans(*rows: tuple[str, str | dt.date]) -> pl.DataFrame:
    frame = pl.DataFrame({"code": [c for c, _ in rows], "first_date": [_date(f) for _, f in rows]})
    return frame.with_columns(last_date=pl.lit(dt.date(2026, 11, 6)))


def bar_dates(code: str, days: list[str | dt.date]) -> pl.DataFrame:
    return pl.DataFrame({"code": [code] * len(days), "date": [_date(x) for x in days]})


def _date(value: str | dt.date) -> dt.date:
    return d(value) if isinstance(value, str) else value


def rows(frame: pl.DataFrame) -> list[tuple]:
    return frame.select("ex_date", "factor").rows()


def test_parse_adjust_factor_takes_the_back_factor():
    frame = pl.DataFrame(
        {"code": ["sh.600000"], "dividOperateDate": ["2002-07-25"], "foreAdjustFactor": ["0.400000"]}
        | {"backAdjustFactor": ["1.250000"], "adjustFactor": ["1.250000"]}
    )
    assert parse_adjust_factor(frame).rows() == [("600000.SH", d("2002-07-25"), 1.25)]


def test_normalize_starts_each_code_at_its_first_bar():
    three = adj("600001.SH", [("2001-08-27", 1.0), ("2002-07-25", 1.25), ("2003-07-14", 1.5)])
    got = normalize(three, spans(("600001.SH", "2003-01-06")))
    assert rows(got) == [(d("2003-01-06"), 1.25), (d("2003-07-14"), 1.5)]
    assert rows(normalize(adj("600001.SH", []), spans(("600001.SH", "2010-01-04")))) == [(d("2010-01-04"), 1.0)]
    same_day = adj("600000.SH", [("2005-03-01", 1.0), ("2010-01-04", 2.0)])
    assert rows(normalize(same_day, spans(("600000.SH", "2010-01-04")))) == [(d("2010-01-04"), 2.0)]
    assert normalize(three, spans(("600000.SH", "2003-01-06"))).filter(pl.col("code") == "600001.SH").is_empty()


def test_events_map_to_the_next_bar():
    two = adj("600001.SH", [("2003-01-06", 1.25), ("2003-07-14", 1.5)])
    got = events(two, bar_dates("600001.SH", ["2003-01-06", "2003-07-11", "2003-07-15"]))
    assert got.select("day", "ex_date").rows() == [(d("2003-07-15"), d("2003-07-14"))]
    assert got["step"][0] == pytest.approx(1.2)  # 1.5 / 1.25


def test_two_ex_dates_in_one_suspension_make_one_event():
    got = events(adj("600000.SH", [(D1, 1.0), (D2, 1.1), (D3, 1.21)]), bar_dates("600000.SH", [D1, "2024-01-08"]))
    assert got.select("day", "ex_date", "factor").rows() == [(d("2024-01-08"), D3, 1.21)]
    assert got["step"][0] == pytest.approx(1.21)


def test_an_ex_date_after_the_last_bar_has_no_day():
    got = events(adj("600000.SH", [(D1, 1.0), (D3, 1.5)]), bar_dates("600000.SH", [D1, D2]))
    assert got.select("day", "ex_date", "step").rows() == [(None, D3, None)]


@pytest.mark.parametrize(
    "check, factors",
    [
        ("ex_date", [(D1, 1.0), (D2, 1.1), (D2, 1.2)]),
        ("factor", [(D1, 1.0), (D2, 0.0)]),
        ("code", [(D1, 1.0)]),
    ],
)
def test_bad_factor_files_raise(tmp_path, check, factors):
    raw = BaoStockRaw(tmp_path)
    write_factors(raw, "600000.SH", factors)
    if check == "code":
        (tmp_path / "adjust_factor" / "600000.SH.parquet").rename(tmp_path / "adjust_factor" / "600004.SH.parquet")
    with pytest.raises(RawDataError, match=rf"\n{check}: "):
        read_factors(raw, raw.adjust_factor_codes())


def test_read_factors_drops_repeated_rows(tmp_path):
    raw = BaoStockRaw(tmp_path)
    write_factors(raw, "600000.SH", [(D1, 1.0), (D2, 1.1), (D2, 1.1)])
    assert rows(read_factors(raw, ["600000.SH"])) == [(D1, 1.0), (D2, 1.1)]
    assert read_factors(raw, []).is_empty()
