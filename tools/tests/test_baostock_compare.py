import datetime as dt

import polars as pl

from tools import baostock_backfill as bf
from tools import baostock_compare as cmp
from tools.baostock_compare import compare, refetch_path
from wequant.data.raw import BaoStockRaw, raw_frame

FIELDS = ["date", "code", "open", "close", "volume", "amount"]
DAY, SAME_DAY, LONE_DAY = dt.date(2020, 7, 21), dt.date(2020, 6, 17), dt.date(2020, 11, 9)
OLD = [
    ["2020-07-21", "sz.300846", "18.4600", "18.4600", "152966", "2052714.18"],
    ["2020-07-21", "sz.300847", "21.4200", "21.4200", "2275128", "1713846.75"],
    ["2020-07-21", "sh.600000", "11.5900", "11.6000", "100", "1160.00"],
    ["2020-07-21", "sh.600004", "15.0400", "15.1000", "200", "3020.00"],
]
NEW = [
    OLD[0],  # the same
    [*OLD[1][:5], "48733241.76"],  # amount changed
    [*OLD[2][:3], "11.6100", "100", "1161.00"],  # close and amount changed
    ["2020-07-21", "sh.600006", "4.3100", "4.3500", "300", "1300.00"],  # sh.600004 gone, sh.600006 new
]
SAME = [
    ["2020-06-17", "sz.000001", "13.0000", "13.1000", "1000", "13050.00"],
    ["2020-06-17", "sz.000002", "27.0000", "27.5000", "2000", "54500.00"],
]
RESOLUTIONS = """
[[resolution]]
code = "300846.SZ"
date = 2020-07-21
kind = "average_price"
fields = ["amount"]
status = "proposed"

[[resolution]]
code = "300847.SZ"
date = 2020-07-21
kind = "average_price"
fields = ["volume", "amount"]
status = "proposed"

[[resolution]]
code = "600000.SH"
date = 2020-07-21
kind = "price_mismatch"
fields = ["open"]

[[resolution]]
code = "600000.SH"
date = 2020-07-21
kind = "price_mismatch"
fields = ["close"]

[[resolution]]
code = "600004.SH"
date = "2020-07-21"
kind = "price_mismatch"
fields = ["close"]

[[resolution]]
code = "000671.SZ"
date = 2019-03-27
kind = "price_mismatch"
fields = ["high"]
"""


def write(raw, path, rows, fetched, **extra):
    """A raw snapshot file of these rows, fetched at `fetched`, with constant extra columns."""
    frame = pl.DataFrame(rows, schema={f: pl.String for f in FIELDS}, orient="row").with_columns(
        **{name: pl.lit(value) for name, value in extra.items()}
    )
    request = f"query_daily_history_k_AStock(date={rows[0][0]})"
    raw.write(path, raw_frame(frame, request, "0.9.4", dt.datetime.fromisoformat(fetched)))


def make_home(tmp_path):
    raw = BaoStockRaw.at(tmp_path)
    write(raw, raw.daily(DAY), OLD, "2026-11-08T23:33:12+00:00")
    write(raw, refetch_path(raw, DAY), NEW, "2026-11-10T08:00:00+00:00", peTTM="12.3")
    write(raw, raw.daily(SAME_DAY), SAME, "2026-11-08T23:40:00+00:00")
    write(raw, refetch_path(raw, SAME_DAY), SAME, "2026-11-10T08:01:00+00:00")
    write(raw, refetch_path(raw, LONE_DAY), SAME, "2026-11-10T08:02:00+00:00")
    write(raw, raw.daily(dt.date(2020, 7, 22)), OLD, "2026-11-08T23:33:12+00:00")  # not refetched: left out
    return tmp_path


def test_compare_counts_identical_changed_added_and_removed_rows(tmp_path):
    text = compare(make_home(tmp_path))
    assert "3 refetched days" in text
    assert "| 2020-06-17 | 2 | 2 | 0 | 0 | 2 | 0 |" in text  # only _fetched_at differs
    assert "| 2020-07-21 | 4 | 4 | 1 | 1 | 1 | 2 |" in text
    assert "| 2020-11-09 | no file | | | | | |" in text
    assert "## 2020-06-17" not in text and "## 2020-11-09" not in text
    assert "- Differing rows: 2 (close 1, amount 2)" in text
    assert "- Only in daily: 1 (sh.600004)" in text and "- Only in refetch: 1 (sh.600006)" in text
    assert "- Columns in one file only: peTTM" in text
    assert "| sz.300847 | amount | 1713846.75 | 48733241.76 |" in text
    assert "| sh.600000 | close | 11.6000 | 11.6100 |" in text and "| sh.600000 | amount | 1160.00 | 1161.00 |" in text
    assert "sz.300846 |" not in text  # identical rows are not sampled
    assert "## Listed rows" not in text


def test_compare_samples_the_first_differing_rows_by_code(tmp_path, monkeypatch):
    monkeypatch.setattr(cmp, "SAMPLES", 1)
    text = compare(make_home(tmp_path))
    assert "| sh.600000 | close | 11.6000 | 11.6100 |" in text and "| sz.300847 |" not in text


def test_compare_lists_the_resolution_rows_and_whether_they_reproduce(tmp_path):
    rows = tmp_path / "resolutions.toml"
    rows.write_text(RESOLUTIONS, encoding="utf-8")
    text = compare(make_home(tmp_path), rows)
    assert "4 of 5 listed (code, date, kind) fall on a refetched day" in text
    lines = text[text.index("## Listed rows (resolutions.toml)") :].splitlines()
    assert lines[-4:] == [
        "| 300846.SZ | 2020-07-21 | average_price | amount 2052714.18, volume 152966 "
        "| amount 2052714.18, volume 152966 | yes |",
        "| 300847.SZ | 2020-07-21 | average_price | volume 2275128, amount 1713846.75 "
        "| volume 2275128, **amount 48733241.76** | no |",
        "| 600000.SH | 2020-07-21 | price_mismatch | open 11.5900, close 11.6000, volume 100, amount 1160.00 "
        "| open 11.5900, **close 11.6100**, volume 100, **amount 1161.00** | no |",
        "| 600004.SH | 2020-07-21 | price_mismatch | close 15.1000, volume 200, amount 3020.00 | no row | no refetch row |",
    ]


def test_the_compare_command_writes_its_report(tmp_path, monkeypatch):
    monkeypatch.setenv("WEQUANT_HOME", str(make_home(tmp_path)))
    out = tmp_path / "compare.md"
    assert bf.main(["compare", "--out", str(out)]) == 0
    assert out.read_text("utf-8") == compare(tmp_path)
