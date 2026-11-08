import datetime as dt
import hashlib
import json

import polars as pl
import pytest

from wequant.data.schema import TABLES
from wequant.data.store import Store, wequant_home


def bars(rows):
    """rows: (code, date, close) -> a full bars frame."""
    df = pl.DataFrame(
        {
            "code": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": pl.Series([r[2] for r in rows], dtype=pl.Int32),
        }
    )
    return df.with_columns(
        open=pl.col("close"),
        high=pl.col("close"),
        low=pl.col("close"),
        volume=pl.lit(100, dtype=pl.Int64),
        amount=pl.lit(1000.0),
        preclose=pl.lit(None, dtype=pl.Int32),
        preclose_derived=pl.lit(True),
        tradestatus=pl.lit(None, dtype=pl.Int8),
        is_st=pl.lit(None, dtype=pl.Boolean),
        source=pl.lit("test"),
    ).select(list(TABLES["bars"].schema))


D1, D2, D3 = dt.date(2023, 12, 29), dt.date(2024, 1, 2), dt.date(2024, 1, 3)
META = {"last_trade_date": "2024-01-03", "source": "test", "release_tag": "t1"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def test_home_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WEQUANT_HOME", str(tmp_path))
    assert wequant_home() == tmp_path
    monkeypatch.delenv("WEQUANT_HOME")
    assert wequant_home().name == ".wequant"


def test_commit_writes_versioned_year_files_and_manifest(store):
    df = bars([("600000.SH", D2, 10_000), ("000001.SZ", D1, 9_000), ("600000.SH", D1, 9_900)])
    result = store.commit({"bars": df}, META)
    assert result.changed and result.version == 1
    assert sorted(result.files_written) == ["bars/2023.v1.parquet", "bars/2024.v1.parquet"]
    manifest = store.manifest()
    assert manifest["version"] == 1 and manifest["release_tag"] == "t1"
    for entry in manifest["tables"]["bars"]["files"]:
        data = (store.home / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert json.loads((store.home / "manifests" / "manifest.v1.json").read_text()) == manifest
    out = store.read_table("bars")
    assert out.select("code", "date").rows() == [("000001.SZ", D1), ("600000.SH", D1), ("600000.SH", D2)]


def test_same_input_twice_is_a_no_op(store):
    df = bars([("600000.SH", D1, 1), ("600000.SH", D2, 2)])
    first = store.commit({"bars": df}, META)
    second = store.commit({"bars": df.reverse()}, META)
    assert second.hashes == first.hashes
    assert not second.changed and second.version == 1 and second.files_written == []
    assert store.versions() == [1]


def test_copy_on_write_keeps_old_files_and_versions(store):
    store.commit({"bars": bars([("600000.SH", D1, 1), ("600000.SH", D2, 2)])}, META)
    v1 = store.manifest()
    old_2024 = next(e for e in v1["tables"]["bars"]["files"] if e["part"] == "2024")
    old_bytes = (store.home / old_2024["path"]).read_bytes()

    result = store.commit({"bars": bars([("600000.SH", D1, 1), ("600000.SH", D2, 2), ("600000.SH", D3, 3)])}, META)
    assert result.version == 2
    assert result.files_written == ["bars/2024.v2.parquet"]  # 2023 is unchanged and reused
    assert result.files_reused == ["bars/2023.v1.parquet"]
    assert (store.home / old_2024["path"]).read_bytes() == old_bytes
    assert store.read_table("bars", version=1).height == 2
    assert store.read_table("bars").height == 3


def test_tables_not_in_a_commit_are_carried_over(store):
    cal = pl.DataFrame({"date": [D1, D2]})
    store.commit({"calendar": cal}, META)
    store.commit({"bars": bars([("600000.SH", D1, 1)])}, META)
    assert set(store.manifest()["tables"]) == {"calendar", "bars"}
    assert store.read_table("calendar")["date"].to_list() == [D1, D2]


def test_exact_duplicates_are_dropped_and_conflicts_raise(store):
    row = ("600000.SH", D1, 1)
    store.commit({"bars": bars([row, row])}, META)
    assert store.read_table("bars").height == 1
    with pytest.raises(ValueError, match="conflicting"):
        store.commit({"bars": bars([row, ("600000.SH", D1, 2)])}, META)


def test_schema_is_enforced(store):
    with pytest.raises(ValueError, match="schema"):
        store.commit({"bars": bars([("600000.SH", D1, 1)]).drop("is_st")}, META)
    with pytest.raises(ValueError, match="nulls"):
        store.commit({"calendar": pl.DataFrame({"date": pl.Series([None], dtype=pl.Date)})}, META)


def test_cleanup_keeps_the_newest_versions_and_deletes_only_when_asked(store):
    for close in range(1, 5):
        store.commit({"bars": bars([("600000.SH", D2, close)])}, META)
    (store.home / "bars" / ".leftover.tmp").write_bytes(b"")
    doomed = store.cleanup(keep=3)
    names = sorted(p.relative_to(store.home).as_posix() for p in doomed)
    assert names == ["bars/.leftover.tmp", "bars/2024.v1.parquet", "manifests/manifest.v1.json"]
    assert all(p.exists() for p in doomed)  # dry run by default
    store.cleanup(keep=3, dry_run=False)
    assert not any(p.exists() for p in doomed)
    assert store.versions() == [2, 3, 4]
    assert store.read_table("bars", version=2)["close"].to_list() == [2]
