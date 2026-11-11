import datetime as dt
import hashlib
import json
import multiprocessing
import os
import socket

import polars as pl
import pytest

from wequant.data import store as store_module
from wequant.data.schema import TABLES
from wequant.data.store import Store, StoreLocked, tables_digest

META = {"last_trade_date": "2003-12-31", "source": "test", "release_tag": "t1"}


def bars(rows):
    """rows: (code, date or ISO string, close) -> a full bars frame."""
    df = pl.DataFrame(
        {
            "code": [r[0] for r in rows],
            "date": [dt.date.fromisoformat(r[1]) if isinstance(r[1], str) else r[1] for r in rows],
            "close": pl.Series([r[2] for r in rows], dtype=pl.Int32),
        },
        schema={"code": pl.String, "date": pl.Date, "close": pl.Int32},
    )
    return df.with_columns(
        open=pl.col("close"),
        high=pl.col("close"),
        low=pl.col("close"),
        volume=pl.lit(100, dtype=pl.Int64),
        amount=pl.lit(1000.0),
        turn=pl.lit(None, dtype=pl.Float64),
        preclose=pl.lit(None, dtype=pl.Int32),
        preclose_derived=pl.lit(True),
        tradestatus=pl.lit(None, dtype=pl.Int8),
        is_st=pl.lit(None, dtype=pl.Boolean),
        source=pl.lit("test"),
    ).select(list(TABLES["bars"].schema))


def four_years():
    return bars(
        [
            (code, f"{y}-06-0{d}", y * 10 + d)
            for y in range(2000, 2004)
            for code in ("600000.SH", "000001.SZ")
            for d in (1, 2)
        ]
    )


def entries(store, table, version=None):
    return {e["part"]: e for e in store.manifest(version)["tables"][table]["files"]}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def reads(monkeypatch):
    """Paths of every Parquet file read through polars."""
    seen = []
    real = pl.scan_parquet

    def scan(source, *args, **kwargs):
        seen.extend(os.path.basename(str(p)) for p in (source if isinstance(source, list) else [source]))
        return real(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", scan)
    monkeypatch.setattr(pl, "read_parquet", lambda *a, **k: pytest.fail("read_parquet bypasses scan"))
    return seen


# ---------------------------------------------------------------- write modes


def test_replace_writes_versioned_year_files_and_manifest(store):
    result = store.commit(META, replace={"bars": four_years()})
    assert result.changed and result.version == 1
    assert result.files_written == [f"bars/{y}.v1.parquet" for y in range(2000, 2004)]
    manifest = store.manifest()
    assert manifest["release_tag"] == "t1"
    for entry in manifest["tables"]["bars"]["files"]:
        assert hashlib.sha256((store.home / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert json.loads((store.home / "manifests" / "manifest.v1.json").read_text()) == manifest
    assert store.read_table("bars").height == 16


def test_replace_drops_parts_the_new_frame_does_not_cover(store):
    store.commit(META, replace={"bars": four_years()})
    store.commit(META, replace={"bars": bars([("600000.SH", "2001-06-01", 1)])})
    assert list(entries(store, "bars")) == ["2001"]
    assert store.read_table("bars").height == 1


def test_upsert_touches_only_its_year(store, reads):
    calendar = pl.DataFrame({"date": [dt.date(2003, 1, 2)]})
    store.commit(META, replace={"bars": four_years(), "calendar": calendar})
    before = entries(store, "bars")
    reads.clear()
    result = store.commit(
        META,
        upsert={"bars": bars([("600000.SH", "2003-06-01", -1), ("600000.SH", "2003-06-03", 99)])},
    )
    assert reads == ["2003.v1.parquet"]
    assert result.files_written == ["bars/2003.v2.parquet"]
    assert sorted(result.files_kept) == [
        *(f"bars/{y}.v1.parquet" for y in (2000, 2001, 2002)),
        "calendar/calendar.v1.parquet",
    ]
    assert set(store.manifest()["tables"]) == {"bars", "calendar"}  # a table the commit leaves out is carried over
    after = entries(store, "bars")
    assert {p: after[p] for p in ("2000", "2001", "2002")} == {p: before[p] for p in ("2000", "2001", "2002")}
    y2003 = store.read_table("bars", start="2003-01-01").sort("code", "date")
    assert y2003.height == 5
    assert y2003.filter(pl.col("code") == "600000.SH")["close"].to_list() == [-1, 20032, 99]
    assert store.read_table("bars", version=1).height == 16  # the old version is intact


def test_unchanged_upsert_creates_no_version(store):
    store.commit(META, replace={"bars": four_years()})
    result = store.commit(META, upsert={"bars": bars([("600000.SH", "2002-06-01", 20021)])})
    assert not result.changed and result.version == 1 and result.files_written == []
    assert result.files_reused == ["bars/2002.v1.parquet"]
    assert store.versions() == [1]


def test_the_same_content_in_any_memory_layout_is_one_file(store):
    df = four_years()
    store.commit(META, replace={"bars": df})
    pieces = pl.concat([df.slice(0, 5), df.slice(5, 3), df.slice(8)], rechunk=False)
    assert pieces.n_chunks() > 1
    result = store.commit(META, replace={"bars": pieces})
    assert not result.changed and result.files_written == []


def test_replace_parts_swaps_whole_years_only(store):
    store.commit(META, replace={"bars": four_years()})
    result = store.commit(META, replace_parts={"bars": bars([("300750.SZ", "2002-01-05", 7)])})
    assert result.files_written == ["bars/2002.v2.parquet"]
    y2002 = store.read_table("bars", start="2002-01-01", end="2002-12-31")
    assert y2002.select("code", "close").rows() == [("300750.SZ", 7)]
    assert store.read_table("bars").height == 13


def test_code_version_alone_creates_no_version(store):
    store.commit({"source": "a", "release_tag": "t1"}, replace={"bars": four_years()})
    store.commit({"source": "b"}, upsert={"bars": bars([("600000.SH", "2003-06-03", 1)])})
    manifest = store.manifest()
    assert manifest["source"] == "b" and manifest["release_tag"] == "t1"  # meta merges into the previous keys

    store.commit({"w": {"code_version": "a", "n": 1}}, replace={"bars": four_years()})
    assert not store.commit({"w": {"code_version": "b", "n": 1}}, replace={"bars": four_years()}).changed
    assert store.manifest()["w"]["code_version"] == "a"
    assert not store.commit({"code_version": "b"}, replace={"bars": four_years()}).changed
    assert store.commit({"w": {"code_version": "b", "n": 2}}, replace={"bars": four_years()}).changed


def test_meta_compares_as_the_manifest_stores_it(store):
    store.commit({"w": {"span": (1, 2)}}, replace={"bars": four_years()})
    assert not store.commit({"w": {"span": (1, 2)}}, replace={"bars": four_years()}).changed
    before = sorted(store.home.rglob("*"))
    with pytest.raises(TypeError):
        store.commit({"w": {"day": dt.date(2003, 6, 3)}}, upsert={"bars": bars([("600000.SH", "2003-06-03", 1)])})
    assert sorted(store.home.rglob("*")) == before


def test_tables_digest_identifies_named_tables(store):
    store.commit(META, replace={"bars": four_years(), "calendar": pl.DataFrame({"date": [dt.date(2003, 1, 2)]})})
    before = store.manifest()
    store.commit(META, upsert={"calendar": pl.DataFrame({"date": [dt.date(2003, 1, 3)]})})
    after = store.manifest()
    assert tables_digest(after, ["bars"]) == tables_digest(before, ["bars"])  # another table changed
    assert tables_digest(after, ["bars", "calendar"]) != tables_digest(before, ["bars", "calendar"])


def test_duplicates_and_schema(store):
    row = ("600000.SH", "2003-01-02", 1)
    store.commit(META, replace={"bars": bars([row, row])})
    assert store.read_table("bars").height == 1
    with pytest.raises(ValueError, match="conflicting"):
        store.commit(META, upsert={"bars": bars([row, ("600000.SH", "2003-01-02", 2)])})
    with pytest.raises(ValueError, match="schema"):
        store.commit(META, replace={"bars": bars([row]).drop("is_st")})


def test_cleanup_keeps_the_newest_versions_and_deletes_only_when_asked(store):
    for close in range(1, 5):
        store.commit(META, replace={"bars": bars([("600000.SH", "2003-01-02", close)])})
    (store.home / "bars" / ".leftover.tmp").write_bytes(b"")
    doomed = store.cleanup(keep=3)
    assert sorted(p.relative_to(store.home).as_posix() for p in doomed) == [
        "bars/.leftover.tmp",
        "bars/2003.v1.parquet",
        "manifests/manifest.v1.json",
    ]
    assert all(p.exists() for p in doomed)
    store.cleanup(keep=3, dry_run=False)
    assert not any(p.exists() for p in doomed)
    assert store.versions() == [2, 3, 4]


def test_cleanup_keeps_a_file_a_kept_version_still_references(store):
    store.commit(META, replace={"bars": four_years()})  # v1 writes 2000 to 2003
    for close in (1, 2, 3):  # v2 to v4 rewrite 2003 only; 2000 to 2002 stay in their v1 files
        store.commit(META, upsert={"bars": bars([("600000.SH", "2003-06-01", close)])})
    deleted = {p.relative_to(store.home).as_posix() for p in store.cleanup(keep=3, dry_run=False)}
    assert deleted == {"manifests/manifest.v1.json", "bars/2003.v1.parquet"}
    for version in (2, 3, 4):
        assert store.read_table("bars", version=version).height == 16


def test_an_interrupted_commit_leaves_the_last_version_intact(store, monkeypatch):
    """A write that fails anywhere in a commit (a data file, the version manifest, manifest.json) leaves manifest.json
    and every table as the last commit left them, the lock free, and the next commit whole."""
    calendar = pl.DataFrame({"date": [dt.date(2003, 1, 2)]})
    store.commit(META, replace={"bars": four_years()})
    manifest, rows = store.manifest(), store.read_table("bars")
    change = {"bars": bars([("600000.SH", "2003-06-01", -1)]), "calendar": calendar}
    real, failed_at = store_module.atomic_write, []
    for failing in range(1, 10):
        calls = []

        def write(path, data):
            calls.append(path)
            if len(calls) == failing:
                raise OSError("disk full")
            real(path, data)

        monkeypatch.setattr(store_module, "atomic_write", write)
        try:
            store.commit(META, upsert=change)
        except OSError:
            failed_at.append(calls[-1].name)
            assert store.manifest() == manifest
            assert store.read_table("bars").equals(rows) and "calendar" not in store.manifest()["tables"]
            assert (store.home / ".lock").read_text() == ""
            continue
        break
    assert failed_at == ["2003.v2.parquet", "calendar.v2.parquet", "manifest.v2.json", "manifest.json"]
    assert store.manifest()["version"] == 2 and store.read_table("bars").height == 16
    assert store.read_table("calendar").equals(calendar)


# ---------------------------------------------------------------- scan


def test_scan_prunes_columns_and_filters_dates_inclusively(store):
    store.commit(META, replace={"bars": four_years()})
    frame = store.scan("bars", columns=["code", "date", "close"], start="2001-06-02", end="2002-06-01")
    assert isinstance(frame, pl.LazyFrame)
    out = frame.collect()
    assert out.columns == ["code", "date", "close"]
    assert sorted(set(out["date"].to_list())) == [dt.date(2001, 6, 2), dt.date(2002, 6, 1)]
    out = store.read_table("bars", codes=["000001.SZ"])
    assert set(out["code"]) == {"000001.SZ"} and out.height == 8


def test_scan_reads_only_the_years_in_range(store, reads):
    store.commit(META, replace={"bars": four_years()})
    reads.clear()
    store.read_table("bars", start=dt.date(2001, 12, 31), end=dt.date(2002, 1, 1))
    assert sorted(reads) == ["2001.v1.parquet", "2002.v1.parquet"]


def test_scan_of_an_empty_table_has_the_schema(store):
    out = store.read_table("bars", columns=["code", "close"], start="2020-01-01")
    assert out.height == 0 and out.schema == {"code": pl.String, "close": pl.Int32}
    store.commit(META, replace={"bars": bars([("600000.SH", "2003-01-02", 1)])})
    assert store.read_table("bars", start="2020-01-01").schema == TABLES["bars"].schema
    with pytest.raises(ValueError, match="no date column"):
        store.scan("securities", start="2020-01-01")


def test_scan_uses_one_snapshot(store):
    store.commit(META, replace={"bars": four_years()})
    snapshot = store.manifest()
    frame = store.scan("bars", manifest=snapshot)
    store.commit(META, replace={"bars": bars([("600000.SH", "2003-01-02", 1)])})
    assert frame.collect().height == 16  # a scan made before the commit
    assert store.read_table("bars", manifest=snapshot).height == 16  # a scan made after it, on the older snapshot


# ---------------------------------------------------------------- lock


def _hold_lock(home, ready, release):
    with Store(home).lock():
        ready.set()
        release.wait(30)


def test_commit_fails_fast_while_another_process_holds_the_lock(store):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_lock, args=(store.home, ready, release))
    child.start()
    try:
        assert ready.wait(30)
        holder = json.loads((store.home / ".lock").read_text())
        assert holder["pid"] == child.pid and holder["host"] == socket.gethostname() and holder["started"]
        with pytest.raises(StoreLocked) as error:
            store.commit(META, replace={"bars": four_years()}, timeout=0)
        assert error.value.holder["pid"] == child.pid and str(child.pid) in str(error.value)
        assert store.manifest() is None
    finally:
        release.set()
        child.join(30)
    assert store.commit(META, replace={"bars": four_years()}).changed
    assert (store.home / ".lock").read_text() == ""


def test_lock_is_reentrant_within_one_store(store):
    with store.lock():
        assert store.commit(META, replace={"bars": four_years()}).changed
        store.cleanup(dry_run=False)


def test_a_commit_is_stamped_by_the_store_s_clock(tmp_path):
    """The demo home and tests fix the clock; a store reads no environment variable for it."""
    store = Store(tmp_path, clock=lambda: "2026-01-01T00:00:00+00:00")
    store.commit(META, replace={"bars": four_years()})
    assert store.manifest()["created_at"] == "2026-01-01T00:00:00+00:00"
