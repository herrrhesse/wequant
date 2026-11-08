import hashlib
import json
import shutil

import numpy as np
import polars as pl
import pytest

from tools.investment_data import checks, release
from tools.investment_data.convert import ConventionError, convert
from tools.investment_data.run import run_import
from tools.investment_data.tests import synthetic as syn
from wequant.data.schema import TABLES
from wequant.data.store import Store


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    root = tmp_path_factory.mktemp("qlib") / "qlib_bin"
    stocks = syn.write_qlib(root)
    tables, stats = convert(root, syn.TAG)
    return {s.code: s for s in stocks}, tables, stats


def bars_of(tables, qlib_code):
    return tables["bars"].filter(pl.col("code") == qlib_code[2:] + "." + qlib_code[:2]).sort("date")


def day(i):
    return syn.DAYS[i].item()


def test_prices_volume_and_amount_are_recovered(converted):
    stocks, tables, _ = converted
    for code in ("SH600000", "SZ000001", "SZ300750", "BJ430047", "BJ920047"):
        s = stocks[code]
        b = bars_of(tables, code)
        days = [d for d in range(s.start, s.end + 1) if d not in s.suspended]
        keep = [d - s.start for d in days]
        assert b["date"].to_list() == [day(d) for d in days]
        for f in ("open", "high", "low", "close"):
            assert b[f].to_list() == (np.rint(s.raw[f][keep] * 100).astype(np.int64) * 10).tolist(), (code, f)
        assert b["volume"].to_list() == np.rint(s.raw["volume"][keep] * 100).astype(np.int64).tolist()
        amount = s.raw["amount"][keep].astype(np.float32).astype(np.float64) * 1000
        np.testing.assert_allclose(b["amount"].to_numpy(), amount, rtol=1e-12)
    assert tables["bars"]["preclose_derived"].all()
    assert tables["bars"]["source"].unique().to_list() == [f"investment_data@{syn.TAG}"]


def test_index_bars_keep_points_to_0_001(converted):
    stocks, tables, _ = converted
    s = stocks["SH000300"]
    b = tables["index_bars"].filter(pl.col("code") == "000300.SH").sort("date")
    assert b.height == syn.N_DAYS
    np.testing.assert_allclose(b["close"].to_numpy(), np.rint(s.raw["close"] * 1000), atol=1)
    # 1e9-share index volumes are beyond float32's exact range (~1e-7 relative)
    np.testing.assert_allclose(b["volume"].to_numpy(), s.raw["volume"] * 100, rtol=2e-7)
    assert "000300.SH" not in tables["securities"]["code"].to_list()


def test_adj_factor_records_real_events_only(converted):
    _, tables, stats = converted
    adj = {code: g.sort("ex_date") for (code,), g in tables["adj_factor"].group_by("code")}
    assert adj["600000.SH"]["ex_date"].to_list() == [day(0), day(20)]
    np.testing.assert_allclose(adj["600000.SH"]["factor"].to_numpy(), [1.2, 1.2 * 1.02], rtol=1e-3)
    assert adj["000001.SZ"]["ex_date"].to_list() == [day(5)]  # the day-25 blip is skipped
    assert adj["300750.SZ"]["ex_date"].to_list() == [day(0)]  # the 5e-5 drift is below the minimum step
    assert adj["430047.BJ"].height == adj["920047.BJ"].height == 1
    a = stats["adjustment"]
    assert [b[:2] for b in a["blips"]] == [("000001.SZ", str(syn.DAYS[25]))]
    assert a["merged"] >= 1
    blip = tables["data_issues"].filter(pl.col("kind") == "adj_blip")
    assert blip.select("table", "code", "date", "action").rows() == [
        ("adj_factor", "000001.SZ", day(25), "excluded_from_adj_factor_and_preclose")
    ]
    assert json.loads(blip["detail"].item())["ratio_blip"] == pytest.approx(1.0, abs=1e-3)
    assert day(25) in bars_of(tables, "SZ000001")["date"].to_list()  # the bar itself is kept


def test_preclose_follows_previous_close_and_ratio(converted):
    stocks, tables, stats = converted
    close = stocks["SH600000"].raw["close"]
    pre = dict(bars_of(tables, "SH600000").select("date", "preclose").iter_rows())
    assert pre[day(0)] is None
    assert pre[day(5)] == round(close[4] * 100) * 10
    assert pre[day(11)] == round(close[9] * 100) * 10  # day 10 is suspended
    assert pre[day(20)] == round(round(close[19] / 1.02, 2) * 100) * 10  # ex-dividend day
    z = stocks["SZ000001"]
    zpre = dict(bars_of(tables, "SZ000001").select("date", "preclose").iter_rows())
    for d in (25, 26):
        assert zpre[day(d)] == round(z.raw["close"][d - 1 - z.start] * 100) * 10
    assert stats["adjustment"]["preclose_mismatch"] == 0


def test_securities_calendar_members_and_checks(converted):
    _, tables, stats = converted
    sec = tables["securities"].sort("code")
    assert sec.select("code", "exchange", "board").rows() == [
        ("000001.SZ", "SZ", "main"),
        ("300750.SZ", "SZ", "chinext"),
        ("430047.BJ", "BJ", "bse"),
        ("600000.SH", "SH", "main"),
        ("920047.BJ", "BJ", "bse"),
    ]
    assert sec.filter(pl.col("code") == "430047.BJ")["last_date"].item() == day(29)
    assert tables["calendar"]["date"].to_list() == [d.item() for d in syn.DAYS]
    members = tables["index_members"]
    assert members.height == sum(len(v) for v in syn.CSI.values())
    assert set(members["index_code"]) == {"399300.SZ", "000905.SH", "000906.SH", "000852.SH", "000985.SH"}
    assert stats["kinds"]["counts"] == {"stock": 5, "index": 1, "fund": 0, "unknown": 0}
    assert stats["kinds"]["bse"] == {"old": 1, "new": 1}
    assert stats["first_close"]["within_1e-6"] == 1.0
    assert all(share == 1.0 for _, share in stats["units"]["by_year"].values())
    assert sum(stats["ohlc"]["bars"]["counts"].values()) == 0


def test_wrong_units_stop_the_conversion(tmp_path):
    root = tmp_path / "qlib_bin"
    syn.write_qlib(root)
    path = root / "features" / "sh600000" / "amount.day.bin"
    data = np.fromfile(path, dtype="<f4")
    data[1:] *= 1000  # yuan instead of 千元
    data.tofile(path)
    with pytest.raises(ConventionError, match="unit check") as error:
        convert(root, syn.TAG)
    assert error.value.stats["units"]["dropped"][0][0] == "600000.SH"


def scale_feature(root, code, field, factor, days=None):
    path = root / "features" / code.lower() / f"{field}.day.bin"
    data = np.fromfile(path, dtype="<f4")
    values = data[1:]
    values[slice(None) if days is None else days] *= factor
    data.tofile(path)


def test_rows_with_a_unit_error_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "UNIT_YEAR_MIN", 0.9)  # one row is a large share of a tiny dataset
    root = tmp_path / "qlib_bin"
    syn.write_qlib(root)
    scale_feature(root, "SZ300750", "volume", 10, days=[7])
    scale_feature(root, "SZ000001", "volume", 10, days=[1])  # its second day: SZ000001 starts on day 5
    tables, stats = convert(root, syn.TAG)
    assert day(7) not in bars_of(tables, "SZ300750")["date"].to_list()
    assert bars_of(tables, "SZ300750").height == syn.N_DAYS - 1
    assert sorted(d[:2] for d in stats["units"]["dropped"]) == [("000001.SZ", 1), ("300750.SZ", 1)]
    off = tables["data_issues"].filter(pl.col("kind") == "off_scale").sort("code")
    assert off.select("table", "code", "date", "action").rows() == [
        ("bars", "000001.SZ", day(6), "row_dropped"),
        ("bars", "300750.SZ", day(7), "row_dropped"),
    ]
    details = [json.loads(d) for d in off["detail"]]
    assert [d["days_since_first"] for d in details] == [1, 7]
    assert all(d["scale"] == pytest.approx(0.1, rel=0.2) for d in details)
    assert [row[:2] for row in stats["issues"]["young_off_scale"]] == [("000001.SZ", str(syn.DAYS[6]))]


def test_a_stock_with_every_row_dropped_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "UNIT_YEAR_MIN", 0.9)
    root = tmp_path / "qlib_bin"
    syn.write_qlib(root)
    scale_feature(root, "BJ920047", "amount", 10)
    tables, stats = convert(root, syn.TAG)
    for name in ("bars", "securities", "adj_factor"):
        assert "920047.BJ" not in tables[name]["code"].to_list()
    assert stats["coverage"]["removed_stocks"] == ["920047.BJ"]
    removed = tables["data_issues"].filter(pl.col("code") == "920047.BJ")
    assert removed["date"].to_list() == [day(d) for d in range(31, syn.N_DAYS)]
    assert set(removed.select("table", "kind", "action").rows()) == {("bars", "removed_security", "security_removed")}


def test_index_member_spans_with_malformed_codes_are_dropped(tmp_path):
    root = tmp_path / "qlib_bin"
    syn.write_qlib(root)
    with open(root / "instruments" / "csi300.txt", "a") as f:
        f.write(f"SHT00018\t{syn.DAYS[0]}\t{syn.DAYS[3]}\n")
    tables, stats = convert(root, syn.TAG)
    assert tables["index_members"].height == sum(len(v) for v in syn.CSI.values())
    assert stats["coverage"]["dropped_members"] == [("csi300.txt", "SHT00018", str(syn.DAYS[0]), str(syn.DAYS[3]))]
    issue = tables["data_issues"].filter(pl.col("kind") == "invalid_member_code")
    assert issue.select("table", "code", "date", "action").rows() == [
        ("index_members", "SHT00018", day(0), "span_dropped")
    ]
    assert json.loads(issue["detail"].item()) == {"list": "csi300.txt", "end_date": str(syn.DAYS[3])}


def test_verify_and_extract(tmp_path):
    raw = tmp_path / "raw"
    syn.write_release(raw, tmp_path / "src")
    result = release.verify(syn.TAG, raw)
    assert result["calendar_last"] == str(syn.DAYS[-1]) and result["future_days"] == 5
    assert release.extract(raw)["status"] == "extracted and verified"
    assert (raw / "extracted" / "calendars" / "day.txt").exists()
    assert release.extract(raw)["status"] == "already extracted, re-verified"
    target = raw / "extracted" / "features" / "sh600000" / "close.day.bin"
    target.write_bytes(target.read_bytes()[:-4])
    with pytest.raises(release.VerificationError, match="does not match"):
        release.extract(raw)


def test_verification_failures(tmp_path):
    raw = tmp_path / "raw"
    syn.write_release(raw, tmp_path / "src")
    with pytest.raises(release.VerificationError, match="release_tag"):
        release.verify("2024-03-02", raw)
    archive = raw / release.ARCHIVE
    data = bytearray(archive.read_bytes())
    data[-10] ^= 0xFF
    archive.write_bytes(bytes(data))
    with pytest.raises(release.VerificationError, match="sha256"):
        release.verify(syn.TAG, raw)

    unsafe = tmp_path / "unsafe"
    syn.write_release(unsafe, tmp_path / "src2")
    syn.add_member(unsafe / release.ARCHIVE, "qlib_bin/../evil.txt")
    manifest = json.loads((unsafe / release.MANIFEST).read_text())
    blob = (unsafe / release.ARCHIVE).read_bytes()
    manifest["archive_size_bytes"] = len(blob)
    manifest["archive_sha256"] = "sha256:" + hashlib.sha256(blob).hexdigest()
    (unsafe / release.MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(release.VerificationError, match="unsafe"):
        release.verify(syn.TAG, unsafe)


def test_run_import_end_to_end_is_idempotent(tmp_path):
    home = tmp_path / "home"
    raw = home / "raw" / "investment_data" / syn.TAG
    syn.write_release(raw, tmp_path / "src")
    code, report = run_import(syn.TAG, home)
    assert code == 0, report.read_text()
    assert "Re-import of the same input:** PASS" in report.read_text()
    manifest = Store(home).manifest()
    assert manifest["version"] == 1 and manifest["release_tag"] == syn.TAG
    assert manifest["last_trade_date"] == str(syn.DAYS[-1])
    assert set(manifest["tables"]) == set(TABLES)
    assert run_import(syn.TAG, home, check_idempotency=False)[0] == 0
    shutil.rmtree(raw / "extracted")
    assert run_import(syn.TAG, home, check_idempotency=False)[0] == 0  # re-extracted: same data
    assert Store(home).manifest()["version"] == 1


def test_off_scale_keeps_wide_first_days_and_drops_unit_errors():
    from tools.investment_data.checks import off_scale
    from tools.investment_data.convert import Recovered

    # a first trading day (low 20.40, high 202.15, close 98.02, average 26.97), a normal day,
    # a day whose amount is 10x off, and a limit-locked day with a consistent average price
    low, high, close, average = (
        np.array(v)
        for v in (
            [20.40, 10.0, 10.0, 12.06],
            [202.15, 11.0, 11.0, 12.06],
            [98.02, 10.5, 10.5, 12.06],
            [26.97, 10.6, 106.0, 12.06],
        )
    )
    lots = np.full(4, 1000.0)
    rec = Recovered(
        codes=["301487.SZ"],
        kinds=np.array(["stock"]),
        spans=[],
        calendar=syn.DAYS,
        code_idx=np.zeros(4, np.int32),
        dates=syn.DAYS[:4],
        price={"low": low, "high": high, "close": close, "open": close, "vwap": average},
        lots=lots,
        amount_k=average * lots * 100 / 1000,
        adjclose=close.astype(np.float32),
        stored_close=close,
    )
    assert off_scale(rec).tolist() == [False, False, True, False]
