import json

import polars as pl
import pytest

from tests.baostock_raw_fake import C1, D1, D2, D3, SEED, SOURCE
from tests.frames import table_frame
from wequant.data.merge.audit import audit_bars, audit_events, check_adjustment, status_disagreements
from wequant.data.merge.factors import events
from wequant.data.merge.reconcile import drop_duplicate_codes


def bars(rows: list[tuple], derived: bool, source: str) -> pl.DataFrame:
    """rows: (code, date, close, extra column values)."""
    full = [
        {"code": c, "date": day, "open": close, "high": close, "low": close, "close": close, "volume": 1000}
        | {"amount": 1e4, "preclose": 10000}
        | extra
        for c, day, close, extra in rows
    ]
    return table_frame("bars", full, preclose_derived=derived, source=source)


def keys(issues: pl.DataFrame) -> set[tuple]:
    return set(issues.select("code", "date", "kind").rows())


def detail(issues: pl.DataFrame, code, day, kind) -> dict:
    row = issues.filter((pl.col("code") == code) & (pl.col("date") == day) & (pl.col("kind") == kind))
    return json.loads(row["detail"].item())


def test_audit_bars():
    new = bars(
        [
            ("600000.SH", D1, 10000, {"amount": 10_150_000.0}),
            ("600000.SH", D2, 10000, {"volume": 1_200_000}),
            ("600000.SH", D3, 10000, {"volume": 904_500}),
            *(("000001.SZ", day, 10000, {}) for day in (D1, D2, D3)),
            *(("000002.SZ", day, 5000, {}) for day in (D1, D2)),
            *(("600849.SH", day, 10000, {}) for day in (D1, D2, D3)),
            ("600004.SH", D1, 10010, {}),
            ("600004.SH", D2, 10000, {}),
            ("600005.SH", D2, 10000, {}),
            ("600006.SH", D2, 10000, {}),
        ],
        False,
        SOURCE,
    )
    old = pl.concat(
        [
            bars(
                [
                    ("600000.SH", D1, 10000, {"amount": 10_150_200.0}),
                    ("600000.SH", D2, 10000, {"volume": 1_200_001}),
                    ("600000.SH", D3, 10000, {"volume": 900_000}),
                    *(("000001.SZ", day, 10000, {}) for day in (D1, D3)),
                    *(("000002.SZ", day, 5000, {}) for day in (D1, D2, D3)),
                    *(("600004.SH", day, 10000, {}) for day in (D1, D2)),
                    ("600006.SH", D2, 10000, {"preclose": 10010}),
                ],
                True,
                SEED,
            ),
            bars([("600005.SH", D2, 10000, {"preclose": 10010})], False, "baostock@0.9.3"),
        ]
    )
    status = table_frame(
        "daily_status", [{"code": "000002.SZ", "date": D3, "tradestatus": 0}], is_st=False, source=SOURCE
    )
    securities = table_frame(
        "securities",
        [{"code": c} for c in ("600000.SH", "000001.SZ", "000002.SZ", "600004.SH", "600005.SH", "600006.SH")],
        exchange="SH",
        board="main",
        first_date=C1,
        last_date=D3,
        source=SEED,
    )
    issues, compared = audit_bars(new, old, status, securities, SOURCE)
    assert keys(issues) == {
        ("600000.SH", D1, "amount_mismatch"),
        ("600000.SH", D3, "volume_mismatch"),
        ("000001.SZ", D2, "bar_added"),
        ("000002.SZ", D3, "bar_unconfirmed"),
        *(("600849.SH", day, "bar_added") for day in (D1, D2, D3)),
        ("600005.SH", D2, "preclose_mismatch"),
    }  # 600004.SH's other prices on D1 are a conflict for the resolutions, not an audit issue
    assert detail(issues, "600000.SH", D3, "volume_mismatch") == {
        "store_volume": 900000,
        "volume": 904500,
        "volume_rel": 0.005,
    }
    assert detail(issues, "000001.SZ", D2, "bar_added") == {"position": "inside"}
    assert detail(issues, "600849.SH", D1, "bar_added") == {"position": "new_code"}
    unconfirmed = detail(issues, "000002.SZ", D3, "bar_unconfirmed")
    assert (unconfirmed["store_close"], unconfirmed["store_volume"], unconfirmed["tradestatus"]) == (5000, 1000, 0)
    assert unconfirmed["store_source"] == SEED
    assert detail(issues, "600005.SH", D2, "preclose_mismatch") == {"preclose": 10000, "store_preclose": 10010}
    assert set(issues["action"]) == {"row_added", "row_dropped", "value_replaced"}
    replaced = compared.filter(pl.col("store_preclose_derived") & (pl.col("preclose") != pl.col("store_preclose")))
    assert replaced.select("code", "date").rows() == [("600006.SH", D2)]
    assert compared.filter(pl.col("code") == "600000.SH")["volume_rel"][1] == pytest.approx(1 / 1_200_001)


def after(rows: list[tuple]) -> pl.DataFrame:
    """rows: (code, date, close, preclose or None for a derived row)."""
    return pl.DataFrame(
        {
            "code": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": pl.Series([r[2] for r in rows], dtype=pl.Int32),
            "preclose": pl.Series([r[3] for r in rows], dtype=pl.Int32),
            "preclose_derived": [r[3] is None for r in rows],
        }
    )


def adj(code: str, rows: list[tuple]) -> pl.DataFrame:
    return table_frame("adj_factor", [{"code": code, "ex_date": e, "factor": f} for e, f in rows])


MISPLACED = after([("600010.SH", D1, 10000, None), ("600010.SH", D2, 8100, 8000), ("600010.SH", D3, 8100, 8100)])


def test_check_adjustment_flags_a_misplaced_seed_event():
    issues, checked = check_adjustment(MISPLACED, adj("600010.SH", [(C1, 1.0), (D3, 1.25)]), SOURCE)
    assert keys(issues) == {("600010.SH", D2, "event_missing"), ("600010.SH", D3, "event_unimplied")}
    assert detail(issues, "600010.SH", D2, "event_missing") == {
        "prev_date": "2024-01-02",
        "prev_close": 10000,
        "preclose": 8000,
        "implied_step": 1.25,
        "factor_step": 1.0,
        "ex_date": None,
    }
    unimplied = detail(issues, "600010.SH", D3, "event_unimplied")
    assert (unimplied["factor_step"], unimplied["implied_step"], unimplied["ex_date"]) == (1.25, 1.0, "2024-01-04")
    assert set(issues["action"]) == {"flagged"} and set(issues["table"]) == {"adj_factor"}
    assert checked.height == 2 and not checked["agrees"].any()
    fixed, _ = check_adjustment(MISPLACED, adj("600010.SH", [(C1, 1.0), (D2, 1.25)]), SOURCE)
    assert fixed.is_empty()
    off, _ = check_adjustment(MISPLACED.head(2), adj("600010.SH", [(C1, 1.0), (D2, 1.1)]), SOURCE)
    assert keys(off) == {("600010.SH", D2, "event_step_off")}
    assert detail(off, "600010.SH", D2, "event_step_off")["ex_date"] == "2024-01-03"


def test_check_adjustment_tolerances():
    rows = after(
        [
            ("600001.SH", D1, 1500, None),
            ("600001.SH", D2, 1490, 1490),
            ("600002.SH", D1, 2000, None),
            ("600002.SH", D2, 2000, 2000),
            ("600003.SH", D1, 300000, None),
            ("600003.SH", D2, 297000, 297000),
            ("600004.SH", D1, 300000, None),
            ("600004.SH", D2, 297000, 297000),
        ]
    )
    steps = {"600001.SH": 1.006, "600002.SH": 1.0024, "600003.SH": 1.0115, "600004.SH": 1.0105}
    factors = pl.concat([adj(code, [(D1, 1.0), (D2, step)]) for code, step in steps.items()])
    issues, checked = check_adjustment(rows, factors, SOURCE)
    assert keys(issues) == {("600003.SH", D2, "event_step_off")}
    # 600004.SH agrees by ratio only: 3.95e-4 apart, while round(300000 / 1.0105) = 296883 is 117 厘 off
    assert checked.select("code", "agrees").rows() == [
        ("600001.SH", True),
        ("600002.SH", True),
        ("600003.SH", False),
        ("600004.SH", True),
    ]


def test_check_adjustment_skips_derived_rows_and_first_bars():
    rows = after([("600001.SH", D1, 1000, None), ("600001.SH", D2, 900, None), ("600849.SH", D1, 5000, 4000)])
    issues, checked = check_adjustment(rows, adj("600001.SH", [(D1, 1.0)]), SOURCE)
    assert issues.is_empty() and checked.is_empty()


def test_audit_events():
    days = pl.DataFrame({"date": [D1, D2, D3]}).join(
        pl.DataFrame({"code": [f"60000{i}.SH" for i in range(8)]}), how="cross"
    )
    days = days.filter(~((pl.col("code") == "600005.SH") & (pl.col("date") == D2)))  # suspended on D2
    new = pl.concat(
        [
            adj("600000.SH", [(D1, 1.0), (D2, 1.019417)]),
            adj("600004.SH", [(D1, 1.0), (D2, 1.06)]),
            adj("600005.SH", [(D1, 1.0), (D2, 1.1)]),
            adj("600006.SH", [(D1, 1.0), (D2, 1.2)]),
            adj("600007.SH", [(D1, 1.0)]),
        ]
    )
    old = pl.concat(
        [
            adj("600000.SH", [(D1, 2.0), (D2, 2.0388)]),
            adj("600004.SH", [(D1, 1.0), (D2, 1.05)]),
            adj("600005.SH", [(D1, 1.0), (D3, 1.1)]),
            adj("600006.SH", [(D1, 1.0)]),
            adj("600007.SH", [(D1, 1.0), (D3, 1.3)]),
        ]
    )
    issues = audit_events(events(new, days), events(old, days), SOURCE)
    assert keys(issues) == {
        ("600004.SH", D2, "step_mismatch"),
        ("600006.SH", D2, "event_added"),
        ("600007.SH", D3, "event_unconfirmed"),
    }
    assert dict(zip(issues["kind"], issues["action"], strict=True)) == {
        "step_mismatch": "value_replaced",
        "event_added": "event_added",
        "event_unconfirmed": "event_dropped",
    }
    assert detail(issues, "600004.SH", D2, "step_mismatch") == {
        "ex_date": "2024-01-03",
        "step": 1.06,
        "store_ex_date": "2024-01-03",
        "store_step": 1.05,
    }


def test_status_disagreements():
    new = bars(
        [("600647.SH", D2, 11000, {}), ("600000.SH", D1, 10000, {}), ("600001.SH", D1, 10000, {})], False, SOURCE
    )
    status = table_frame(
        "daily_status",
        [
            {"code": "600647.SH", "date": D2, "tradestatus": 0},
            {"code": "600766.SH", "date": D3, "tradestatus": 1},
            {"code": "600000.SH", "date": D1, "tradestatus": 1},
            {"code": "600002.SH", "date": D1, "tradestatus": 0},
        ],
        is_st=False,
        source=SOURCE,
    )
    got = status_disagreements(new, status)
    assert got.select("code", "date", "tradestatus", "action").rows() == [
        ("600001.SH", D1, None, "bar_kept"),
        ("600647.SH", D2, 0, "bar_kept"),
        ("600766.SH", D3, 1, "no_bar"),
    ]


def test_a_duplicate_with_an_amount_within_amount_rtol_is_dropped():
    # 0.48 yuan apart on 1,000,000.48 is 4.8e-7, inside AMOUNT_RTOL (1e-5); 300115.SZ is 1,000 yuan (1e-3) off
    new = bars(
        [
            ("300114.SZ", D1, 2000, {"amount": 1_000_000.48}),
            ("302132.SZ", D1, 2000, {"amount": 1_000_000.00}),
            ("300115.SZ", D1, 2000, {"amount": 999_000.00}),
        ],
        False,
        SOURCE,
    )
    status = table_frame(
        "daily_status", new.select("code", "date").to_dicts(), tradestatus=1, is_st=False, source=SOURCE
    )
    kept, kept_status, issues = drop_duplicate_codes(new, status, new.head(1), SOURCE)
    assert kept["code"].to_list() == kept_status["code"].to_list() == ["300114.SZ", "300115.SZ"]
    assert keys(issues) == {("302132.SZ", D1, "duplicate_code")}
    assert detail(issues, "302132.SZ", D1, "duplicate_code") == {"same_as": "300114.SZ"}

    # neither code in the store: the bar cannot be attributed, so both are kept
    kept, kept_status, issues = drop_duplicate_codes(new.head(2), status.head(2), new.head(0), SOURCE)
    assert kept.height == 2 and kept_status.height == 2 and issues.is_empty()


@pytest.mark.parametrize("volume, flagged", [(1_000_100, False), (1_000_101, True)])
def test_volumes_within_one_lot_agree(volume, flagged):
    """Sources round volumes to the lot: up to 100 shares apart is agreement, not a volume_mismatch."""
    new = bars([("600000.SH", D2, 10000, {"volume": volume})], False, SOURCE)
    old = bars([("600000.SH", D2, 10000, {"volume": 1_000_000})], True, SEED)
    status = table_frame(
        "daily_status", [{"code": "600000.SH", "date": D2, "tradestatus": 1}], is_st=False, source=SOURCE
    )
    securities = table_frame(
        "securities", [{"code": "600000.SH"}], exchange="SH", board="main", first_date=C1, last_date=D3, source=SEED
    )
    issues, _ = audit_bars(new, old, status, securities, SOURCE)
    assert (("600000.SH", D2, "volume_mismatch") in keys(issues)) is flagged
