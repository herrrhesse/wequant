"""data delistings and data index-check on synthetic stores built from tests.service.conftest.MARKET."""

import datetime as dt

import polars as pl
import pytest

from tests.backtest.market import build_store
from tests.frames import table_frame
from tests.service.conftest import DAYS, MARKET
from wequant.service import Context, ServiceError
from wequant.service.delistings import DelistingsRequest, data_delistings
from wequant.service.index_check import IndexCheckRequest, data_index_check

D = [dt.date.fromisoformat(day) for day in DAYS]
FORCED, MERGED = "600000.SH", "600006.SH"
OVERRIDE = f'["{MERGED}"]\nkind = "merger"\nevidence = "absorbed by 600004.SH"\nsource = "exchange notice"\n'


@pytest.fixture
def delisted_home(tmp_path):
    """600000.SH (named 退市甲) ends on day 5, 600006.SH on day 4 with a merger override; no official dates."""
    market = {**MARKET, FORCED: MARKET[FORCED][:6] + [None] * 2, MERGED: MARKET[MERGED][:5] + [None] * 3}
    store = build_store(tmp_path / "home", DAYS, market)
    code = pl.col("code")
    securities = store.read_table("securities").with_columns(
        name=pl.when(code == FORCED).then(pl.lit("退市甲")).otherwise(pl.lit("某股份")),
        last_date=pl.when(code == FORCED)
        .then(pl.lit(D[5]))
        .when(code == MERGED)
        .then(pl.lit(D[4]))
        .otherwise("last_date"),
    )
    store.commit({}, replace={"securities": securities})
    (store.home / "overrides").mkdir()
    (store.home / "overrides" / "delisting.toml").write_text(OVERRIDE, "utf-8")
    return store.home


def test_every_delisted_code_shows_the_basis_of_its_kind_and_its_treatment(delisted_home):
    found = data_delistings(DelistingsRequest(), Context(delisted_home))
    rows = [(r.code, r.kind, r.classified_by, r.evidence, r.source, r.day, r.approximate) for r in found.rows]
    assert found.overrides and rows == [
        (FORCED, "forced", "rule", None, None, D[6], True),  # the name carries 退
        (MERGED, "merger", "override", "absorbed by 600004.SH", "exchange notice", D[5], True),
    ]
    assert [(r.treatment, r.recovery) for r in found.rows] == [("write_down", 0.0)] * 2  # approximate: never cashed


def test_the_counts_are_the_rows_by_kind_treatment_and_classifier(delisted_home):
    """F-25: the page leads with these; one forced code by the rule and one merger by an override, both approximate."""
    counts = data_delistings(DelistingsRequest(), Context(delisted_home)).counts
    assert counts.model_dump() == {
        "total": 2,
        "kind": {"forced": 1, "merger": 1, "suspension": 0, "unknown": 0},
        "treatment": {"write_down": 2, "exit": 0, "hold": 0},
        "classified_by": {"rule": 1, "override": 1},
        "approximate": 2,
    }


def test_the_treatment_follows_the_execution_block(delisted_home):
    held = data_delistings(DelistingsRequest(execution={"delisting": "hold"}), Context(delisted_home))
    assert [(r.treatment, r.recovery) for r in held.rows] == [("hold", None)] * 2
    with pytest.raises(ServiceError) as raised:
        data_delistings(
            DelistingsRequest(execution={"delisting": "hold", "failure_recovery": 0.5}), Context(delisted_home)
        )
    assert [(d.code, d.loc) for d in raised.value.detail] == [("requires", ["execution", "failure_recovery"])]


def test_index_check_reports_each_run_of_days_off_the_nominal_size(tmp_path):
    store = build_store(tmp_path / "home", DAYS, MARKET)
    spans = [  # CSI 300 (stored as 399300.SZ): three members, one leaving after day 3; CSI All Share: none on days 2-3
        ("399300.SZ", "000001.SZ", D[0], D[7]),
        ("399300.SZ", "600004.SH", D[0], D[7]),
        ("399300.SZ", "600006.SH", D[0], D[3]),
        ("000985.SH", "000001.SZ", D[0], D[1]),
        ("000985.SH", "600004.SH", D[4], D[5]),
    ]
    rows = [dict(zip(("index_code", "code", "start_date", "end_date"), span)) for span in spans]
    store.commit({}, replace={"index_members": table_frame("index_members", rows)})
    found = data_index_check(IndexCheckRequest(), Context(store.home))
    assert not found.ok and [(i.index, i.stored_as, i.nominal, i.first, i.last, i.days) for i in found.indexes] == [
        ("000300.SH", "399300.SZ", 300, D[0], D[7], 8),
        ("000985.SH", "000985.SH", None, D[0], D[5], 6),
    ]
    got = [[(s.start, s.days, s.count, s.kind) for s in i.spans] for i in found.indexes]
    assert got == [[(D[0], 4, 3, "short"), (D[4], 4, 2, "short")], [(D[2], 2, 0, "empty")]]


def test_index_check_without_members_is_ok(tmp_path):
    store = build_store(tmp_path / "home", DAYS, MARKET)
    assert data_index_check(IndexCheckRequest(), Context(store.home)).model_dump() == {
        "ok": True,
        "data_version": 1,
        "indexes": [],
    }
