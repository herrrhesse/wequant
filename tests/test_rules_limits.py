import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import polars as pl

from wequant.rules.limits import FREE, INPUT, LIMITED, UNKNOWN, price_limits

CENT = Decimal("0.01")


def inputs(rows: list[dict]) -> pl.DataFrame:
    """price_limits INPUT rows from dicts with dates or ISO strings; missing fields are null."""
    data = [{c: _iso(row.get(c)) for c in INPUT} for row in rows]
    frame = pl.DataFrame(data, schema=INPUT | {"date": pl.String, "list_date": pl.String})
    return frame.with_columns(pl.col("date", "list_date").str.to_date())


def _iso(value):
    return value.isoformat() if isinstance(value, dt.date) else value


def limits(**row) -> tuple:
    """(limit_up, limit_down, limit_rule, limit_state) of one row."""
    out = price_limits(inputs([{"code": "600000.SH", **row}])).row(0, named=True)
    return out["limit_up"], out["limit_down"], out["limit_rule"], out["limit_state"]


def reference(yuan: list[Decimal], bp: int) -> np.ndarray:
    """Each price moved by bp basis points and rounded half up to the cent by Decimal, in 厘."""
    factor = 1 + Decimal(bp) / 10000
    return np.array([int((y * factor).quantize(CENT, ROUND_HALF_UP) * 1000) for y in yuan])


def test_limits_match_decimal_half_up_for_every_fen_price():
    pre = np.arange(10, 1_000_001, 10, dtype=np.int64)
    yuan = [Decimal(p) / 1000 for p in pre.tolist()]
    cases = {  # up bp, down bp: row fields
        (500, 500): {"board": "main", "date": "2024-02-20", "is_st": True},
        (1000, 1000): {"board": "main", "date": "2024-02-20"},
        (2000, 2000): {"board": "chinext", "date": "2024-02-20"},
        (3000, 3000): {"board": "bse", "date": "2024-02-20"},
        (4400, 3600): {"board": "main", "date": "2014-02-11", "list_date": "2014-02-11", "listing_day": 1},
    }
    for (up_bp, down_bp), fields in cases.items():
        frame = inputs([fields]).drop("preclose").join(pl.DataFrame({"preclose": pre.astype(np.int32)}), how="cross")
        out = price_limits(frame.select(*INPUT))
        up, down = reference(yuan, up_bp), reference(yuan, -down_bp)
        assert (out["limit_up"].to_numpy() == np.maximum(up, pre + 10)).all()
        assert (out["limit_down"].to_numpy() == np.maximum(np.minimum(down, pre - 10), 10)).all()
        assert out["limit_state"].unique().to_list() == [LIMITED]


def test_tick_guards_keep_a_limit_one_tick_from_the_preclose():
    assert limits(date="2024-02-20", board="main", preclose=90, is_st=True)[:2] == (100, 80)
    assert limits(date="2024-02-20", board="main", preclose=40)[:2] == (50, 30)


def test_unknown_st_takes_the_normal_band_and_changes_nothing_else():
    row = {"code": "600000.SH", "date": "2024-02-20", "board": "main", "preclose": 1230}
    st, unknown = price_limits(inputs([{**row, "is_st": True}, row])).iter_rows(named=True)
    # ST 5%: 1291.5 and 1168.5 round to 1290 and 1170; unknown ST takes 10%: 1353 and 1107 round to 1350 and 1110
    assert (st["limit_up"], st["limit_down"], unknown["limit_up"], unknown["limit_down"]) == (1290, 1170, 1350, 1110)
    assert {k: v for k, v in st.items() if k not in ("is_st", "limit_up", "limit_down")} == {
        k: v for k, v in unknown.items() if k not in ("is_st", "limit_up", "limit_down")
    }


def test_new_listing_rules_are_chosen_by_listing_date():
    before = limits(date="2023-04-10", board="main", preclose=10000, list_date="2023-04-07", listing_day=2)
    after = limits(date="2023-04-11", board="main", preclose=10000, list_date="2023-04-10", listing_day=2)
    assert before == (11000, 9000, "band-main-1996", LIMITED)
    assert after == (None, None, "ipo-main-2023", FREE)


def test_a_first_day_cap_without_preclose_is_unknown():
    day1 = limits(date="2014-02-11", board="main", list_date="2014-02-11", listing_day=1)
    assert day1 == (None, None, "ipo-main-2014", UNKNOWN)
    assert limits(date="2024-02-20", board="main") == (None, None, "band-main-1996", UNKNOWN)


def test_days_before_any_rule_are_unknown_without_a_rule():
    assert limits(date="2020-07-24", board="bse", preclose=10000) == (None, None, None, UNKNOWN)


def test_input_order_and_extra_columns_are_kept():
    rows = [
        {"code": "600000.SH", "date": "2024-02-21", "board": "main", "preclose": 10000},
        {"code": "300001.SZ", "date": "2009-10-30", "board": "chinext", "list_date": "2009-10-30", "listing_day": 1},
        {"code": "600000.SH", "date": "2024-02-20", "board": "main", "preclose": 20000},
    ]
    frame = inputs(rows).with_columns(note=pl.Series(["a", "b", "c"])).select("note", *INPUT)
    out = price_limits(frame)
    assert out.columns == ["note", *INPUT, "limit_up", "limit_down", "limit_rule", "limit_state"]
    assert out["note"].to_list() == ["a", "b", "c"]
    assert out["limit_up"].to_list() == [11000, None, 22000]
    assert out["date"].to_list() == [dt.date(2024, 2, 21), dt.date(2009, 10, 30), dt.date(2024, 2, 20)]
