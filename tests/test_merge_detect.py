"""Detections across BaoStock rows: stale volumes, carried amounts, cut snapshots and closes off the next preclose."""

import json

import polars as pl
import pytest

from tests.baostock_raw_fake import C2, D1, D2, D3, SOURCE
from wequant.data.merge.detect import close_flags, marks, truncated_days


def frame(rows: list[tuple], columns: str) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=columns.split(), orient="row")


KEYED = {"code": pl.String, "date": pl.Date, "volume": pl.Int64}
NO_PREVIOUS = pl.DataFrame(schema=KEYED | {"amount": pl.Float64})
NO_SEED = pl.DataFrame(schema=KEYED)


def test_a_suspended_row_is_stale_only_without_an_amount():
    """D9: six rows traded with tradestatus 0; only tradestatus 0 with amount 0 marks a stale volume."""
    bars = frame(
        [("600000.SH", D1, 100, 0.0), ("600001.SH", D1, 100, 500.0), ("600002.SH", D1, 100, 0.0)],
        "code date volume amount",
    )
    status = frame([("600000.SH", D1, 0), ("600001.SH", D1, 0), ("600002.SH", D1, 1)], "code date tradestatus")
    found = marks(bars, status, NO_PREVIOUS, NO_SEED)
    assert found.filter("stale")["code"].to_list() == ["600000.SH"]


def test_an_amount_repeating_the_previous_bar_to_the_fen_at_another_volume_is_carried():
    previous = frame([("600000.SH", C2, 100, 1234.56)], "code date volume amount")
    bars = frame(
        [("600000.SH", D1, 200, 1234.56), ("600000.SH", D2, 300, 1234.564), ("600000.SH", D3, 300, 1234.56)],
        "code date volume amount",
    )
    status = bars.select("code", "date", tradestatus=pl.lit(1))
    found = marks(bars, status, previous, NO_SEED)
    assert found.select("date", "carried").rows() == [(D1, True), (D2, True), (D3, False)]  # D3: the same volume


CUT_DAYS = {  # traded rows with a seed bar, those 0.2% short of the seed's volume -> whether the day looks cut
    "three_percent_and_ten_rows": (400, 12, True),
    "below_three_percent": (401, 12, False),
    "fewer_than_ten_rows": (300, 9, False),
}


@pytest.mark.parametrize("rows, short, cut", CUT_DAYS.values(), ids=CUT_DAYS)
def test_a_day_is_cut_when_enough_volumes_fall_short_of_the_seed(rows, short, cut):
    codes = [f"6{i:05d}.SH" for i in range(rows)]
    volumes = [99_800 if i < short else 99_950 for i in range(rows)]  # 99,950: 0.05% short, inside SHORT_RTOL
    bars = pl.DataFrame({"code": codes, "date": D1, "volume": volumes, "amount": 1.0})
    old = bars.select("code", "date", volume=pl.lit(100_000))
    assert truncated_days(bars, old).height == cut
    found = marks(bars, bars.select("code", "date", tradestatus=pl.lit(1)), NO_PREVIOUS, old)
    assert found.filter("truncated")["code"].to_list() == (codes[:short] if cut else [])


def checked(rows: list[tuple]) -> pl.DataFrame:
    """check_adjustment's checked rows: (code, prev_close, preclose, factor_step), each from D1 to D2."""
    found = frame(rows, "code prev_close preclose factor_step")
    return found.with_columns(date=pl.lit(D2), prev_date=pl.lit(D1))


def test_a_close_the_next_preclose_does_not_repeat_without_a_factor_step_is_flagged_on_its_day():
    rows = checked(
        [("600000.SH", 10000, 10010, 1.0), ("600001.SH", 10000, 9000, 1.1), ("600002.SH", 10000, 10000, 1.0)]
    )
    after = rows.select("code", date=pl.lit(D1))
    flags = close_flags(rows, after, SOURCE).filter(pl.col("kind") == "close_preclose_off")
    assert flags.select("code", "date").rows() == [("600000.SH", D1)]
    assert json.loads(flags["detail"].item()) == {"close": 10000, "next_date": D2.isoformat(), "next_preclose": 10010}


CUT_CLOSES = {  # closes checked against the next exchange preclose, those off -> whether the day looks cut
    "five_percent_and_twenty_rows": (400, 20, True),
    "below_five_percent": (401, 20, False),
    "fewer_than_twenty_rows": (380, 19, False),
}


@pytest.mark.parametrize("rows, off, cut", CUT_CLOSES.values(), ids=CUT_CLOSES)
def test_every_bar_of_a_day_with_enough_closes_off_is_flagged_cut(rows, off, cut):
    found = checked([(f"6{i:05d}.SH", 10000, 10010 if i < off else 10000, 1.0) for i in range(rows)])
    after = pl.concat([found.select("code", date=pl.lit(D1)), frame([("600999.SH", D1)], "code date")])
    flags = close_flags(found, after, SOURCE).filter(pl.col("kind") == "snapshot_truncated")
    assert flags.height == (rows + 1 if cut else 0)  # every bar of the day, checked or not
