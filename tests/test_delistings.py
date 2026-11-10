"""Delisted codes: the day a holding is handled, the rule's kind, the user's overrides, and the panel's table."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tests.backtest.market import build_store, business_days
from wequant.data.delistings import (
    DELIST_DATE,
    DELISTINGS,
    OVERRIDES,
    Override,
    classify,
    delisted,
    overrides_digest,
    parse_overrides,
)
from wequant.panel import load_panel

DAYS = business_days(6)  # 2024-01-02 .. 2024-01-09
CALENDAR = np.array(DAYS, dtype="datetime64[D]")
D = [dt.date.fromisoformat(day) for day in DAYS]
OVERRIDE = '["600003.SH"]\nkind = "merger"\nevidence = "absorbed by 600999.SH"\nsource = "announcement 2024-01-05"\n'


def codes(**columns):
    """Securities columns, last_bar and last_close of four codes, with columns replaced."""
    frame = pl.DataFrame(
        {
            "code": ["600001.SH", "600002.SH", "600003.SH", "600004.SH"],
            "last_date": [D[1], D[1], D[2], D[5]],
            "last_bar": [D[1], D[1], D[2], D[3]],
            "last_close": [3.2, 0.62, 8.0, 5.0],
            "name": ["退市甲", None, "丙股份", None],
        }
    )
    return frame.with_columns(**columns)


def test_without_official_dates_the_day_follows_last_date_and_is_approximate():
    found = classify(codes(), CALENDAR, {})
    assert found.schema == DELISTINGS
    rows = found.select("code", "day", "approximate", "kind", "classified_by").rows()
    assert rows == [
        ("600001.SH", D[2], True, "forced", "rule"),  # 退 in the name
        ("600002.SH", D[2], True, "forced", "rule"),  # a last close below 1 yuan
        ("600003.SH", D[3], True, "unknown", "rule"),  # named, but no official date to call it a merger
        ("600004.SH", None, True, "unknown", "rule"),  # its data ends with the calendar: no day yet
    ]


def test_official_dates_set_the_day_never_before_the_day_after_the_last_bar():
    dates = pl.Series([D[3], None, D[4], dt.date(2024, 1, 6)], dtype=pl.Date)  # the last a Saturday
    found = classify(codes(**{DELIST_DATE: dates, "last_bar": pl.Series([D[3]] * 4)}), CALENDAR, {})
    rows = found.select("code", "delist_date", "day", "approximate", "kind").rows()
    assert rows == [
        ("600001.SH", D[3], D[4], False, "forced"),  # a bar on the official day: the next day
        ("600002.SH", None, None, False, "suspension"),  # the official dates leave it listed
        ("600003.SH", D[4], D[4], False, "merger"),  # named without 退, last close at least 1 yuan
        ("600004.SH", dt.date(2024, 1, 6), D[4], False, "unknown"),  # no name; the next trading day
    ]


def test_an_override_sets_the_kind_and_says_so():
    found = classify(codes(), CALENDAR, parse_overrides(OVERRIDE))
    row = found.filter(pl.col("code") == "600003.SH").row(0, named=True)
    assert (row["kind"], row["classified_by"], row["day"]) == ("merger", "override", D[3])
    assert (row["evidence"], row["source"]) == ("absorbed by 600999.SH", "announcement 2024-01-05")
    assert found.filter(pl.col("classified_by") == "rule")["evidence"].null_count() == 3


def test_delisted_codes_end_before_the_calendar_or_have_an_official_date():
    frame = codes()
    assert delisted(frame, CALENDAR).tolist() == [True, True, True, False]
    official = frame.with_columns(pl.Series(DELIST_DATE, [None, None, None, D[5]], dtype=pl.Date))
    assert delisted(official, CALENDAR).tolist() == [True, True, True, True]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('["600003"]\nkind = "merger"\nevidence = "e"\nsource = "s"\n', "is not a code"),
        ('["600003.SH"]\nkind = "merger"\nevidence = "e"\n', "needs exactly kind, evidence, source"),
        ('["600003.SH"]\nkind = "merger"\nevidence = "e"\nsource = "s"\nnote = "n"\n', "needs exactly"),
        ('["600003.SH"]\nkind = "unknown"\nevidence = "e"\nsource = "s"\n', "kind 'unknown' is not one of"),
        ('["600003.SH"]\nkind = "merger"\nevidence = " "\nsource = "s"\n', "non-empty text"),
        ('["600003.SH"]\nkind = "merger"\nevidence = 1\nsource = "s"\n', "non-empty text"),
        ('["600003.SH"]\nkind = "merger"\nkind = "forced"\n', "overrides/delisting.toml"),
    ],
)
def test_a_malformed_override_names_its_entry(text, error):
    with pytest.raises(ValueError, match=error):
        parse_overrides(text)


def test_no_overrides_file_is_an_empty_table():
    assert parse_overrides(None) == {} and overrides_digest(None) == ""
    assert parse_overrides(OVERRIDE) == {
        "600003.SH": Override("merger", "absorbed by 600999.SH", "announcement 2024-01-05")
    }
    assert overrides_digest(OVERRIDE).startswith("-o") and len(overrides_digest(OVERRIDE)) == 14


def test_the_panel_classifies_its_codes_and_its_key_names_the_overrides(tmp_path):
    home = tmp_path / "home"
    bars = {"600001.SH": [(4.0, 4.0), (3.2, 3.2), *[None] * 4], "600002.SH": [(6.0, 6.0)] * 6}
    store = build_store(home, DAYS, bars)
    ended = pl.when(pl.col("code") == "600001.SH").then(pl.lit(D[1])).otherwise(pl.col("last_date"))
    store.commit({}, replace={"securities": store.read_table("securities").with_columns(last_date=ended)})
    panel = load_panel(store=store, cache_dir=tmp_path / "cache")
    assert panel.delistings.select("code", "day", "last_bar", "last_close", "kind").rows() == [
        ("600001.SH", D[2], D[1], 3.2, "unknown")
    ]
    assert load_panel(codes=["600002.SH"], store=store, cache_dir=tmp_path / "cache").delistings.is_empty()
    (home / OVERRIDES).parent.mkdir()
    (home / OVERRIDES).write_text(OVERRIDE.replace("600003.SH", "600001.SH"), "utf-8")
    overridden = load_panel(store=store, cache_dir=tmp_path / "cache")
    assert overridden.key == panel.key + overrides_digest(OVERRIDE.replace("600003.SH", "600001.SH"))
    assert overridden.delistings["kind"].to_list() == ["merger"]
