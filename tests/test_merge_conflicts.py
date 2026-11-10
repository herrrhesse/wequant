import polars as pl
import pytest

from tests.baostock_raw_fake import C1, D1, D2, D3, SEED, SOURCE, STEADY, World, raw_row
from tests.frames import table_frame
from wequant.data.merge import RawDataError
from wequant.data.merge.conflicts import average_conflicts, price_conflicts, with_references
from wequant.data.merge.propose import proposal

OHLC = ("open", "high", "low", "close")


def bars(rows: list[tuple], source: str) -> pl.DataFrame:
    """rows: (code, close, extra column values) on D1; 1000 shares, amount = volume × close."""
    full = [
        {"code": c, "date": D1, **dict.fromkeys(OHLC, close), "volume": 1000, "amount": float(close)} | extra
        for c, close, extra in rows
    ]
    return table_frame("bars", full, preclose_derived=source == SEED, source=source)


def test_price_and_average_conflicts():
    new = bars(
        [
            ("600000.SH", 10010, {"high": 10010}),  # other close
            ("600001.SH", 10000, {"amount": 0.0}),  # no amount
            ("600002.SH", 10010, {"amount": 20000.0}),  # other close and an amount above 1000 × 10.01
            ("600003.SH", 10000, {"amount": 10011.5}),  # just above the tolerance, no store bar
            ("600004.SH", 10000, {"amount": 10010.5}),  # just inside it
            ("600005.SH", 10000, {"amount": 0.0, "volume": 1_000_000}),  # the seed's volume 1 share off: same
            ("600006.SH", 10000, {"amount": 0.0, "volume": 1_000_000}),  # the seed's volume 2 shares off: other
            ("600007.SH", 10000, {"amount": 0.0}),  # the seed's amount is null
        ],
        SOURCE,
    )
    old = bars(
        [(c, 10000, {}) for c in ("600000.SH", "600001.SH", "600002.SH", "600004.SH")]
        + [("600005.SH", 10000, {"volume": 1_000_001}), ("600006.SH", 10000, {"volume": 1_000_002})]
        + [("600007.SH", 10000, {"amount": None})],
        SEED,
    )
    columns = ("code", "kind", "seed_close", "seed_paid", "same_volume")
    assert price_conflicts(new, old).select(columns).rows() == [
        ("600000.SH", "price_mismatch", 10000, True, True),
        ("600002.SH", "price_mismatch", 10000, True, True),
    ]
    assert average_conflicts(new, old).select(columns).rows() == [
        ("600001.SH", "average_price", 10000, True, True),
        ("600002.SH", "average_price", 10000, True, True),
        ("600003.SH", "average_price", None, False, None),
        # same_volume allows the seed's float rounding (VOLUME_RTOL 1e-6); 10,000 yuan for a million shares fails
        ("600005.SH", "average_price", 10000, False, True),
        ("600006.SH", "average_price", 10000, False, False),
        ("600007.SH", "average_price", 10000, False, True),  # a null seed amount fails the check
    ]


def write_world(path, raw_days=(D1, D2, D3), **raw_text):
    w = World()
    w.stock("600000.SH", STEADY, raw=raw_days)
    if raw_text:
        raw_row(w, "600000.SH", D2, **raw_text)
    return w.write(path)


def references(store, raw, rows, adj_rows=()):
    adj = table_frame("adj_factor", [{"code": "600000.SH", "ex_date": e, "factor": f} for e, f in adj_rows])
    keys = pl.DataFrame({"code": ["600000.SH"] * len(rows), "date": rows})
    calendar = store.read_table("calendar")["date"]
    return with_references(raw, keys, calendar, adj).drop("code").rows()


def test_the_reference_is_the_next_day_s_exchange_preclose(tmp_path):
    store, raw = write_world(tmp_path / "all")
    assert references(store, raw, [D3, D1]) == [(D3, None, False, None, False), (D1, D2, True, 10200, False)]
    store, raw = write_world(tmp_path / "unfetched", raw_days=(D1, D2))  # D3: a calendar day without a snapshot
    assert references(store, raw, [D2]) == [(D2, D3, False, None, False)]


def test_an_adj_factor_step_up_to_the_next_day_spoils_the_reference(tmp_path):
    store, raw = write_world(tmp_path)
    assert references(store, raw, [D1], [(C1, 1.0), (D2, 1.1)]) == [(D1, D2, True, 10200, True)]
    assert references(store, raw, [D1], [(C1, 1.0), (D1, 1.1)]) == [(D1, D2, True, 10200, False)]  # in force before
    assert references(store, raw, [D1], [(C1, 1.0), (D2, 1.0)]) == [(D1, D2, True, 10200, False)]  # no step
    assert references(store, raw, [D1], [(D2, 1.1)]) == [(D1, D2, True, 10200, True)]  # 1.0 before a first row
    assert references(store, raw, [D1], [(D2, 1.0)]) == [(D1, D2, True, 10200, False)]


def test_a_reference_preclose_off_the_fen_raises(tmp_path):
    store, raw = write_world(tmp_path, preclose="10.2050")
    with pytest.raises(RawDataError, match=r"reference preclose: 1 rows, e.g. \[\('600000.SH'"):
        references(store, raw, [D1])
    assert references(store, raw, [D2]) == [(D2, D3, True, 10300, False)]  # D3's row is not D2's


def conflict(kind: str, new: dict, old: dict | None, reference=(D2, True, None, False)) -> dict:
    """The conflict row of one bar on D1 at 10.00 (1000 shares), with a reference (next_date, fetched, preclose,
    ex_event)."""
    stored = bars([("600000.SH", 10000, old)], SEED) if old is not None else bars([], SEED)
    found = price_conflicts if kind == "price_mismatch" else average_conflicts
    row = found(bars([("600000.SH", 10000, new)], SOURCE), stored).row(0, named=True)
    return row | dict(zip(("next_date", "fetched", "next_preclose", "ex_event"), reference, strict=True))


OTHER_CLOSE = {"close": 10010, "high": 10010, "amount": 10010.0}
PRICE_RULES = {  # the next day's exchange preclose (None: no row) and an adj_factor step by then -> take, a fragment
    "repeats_baostock": ((D2, True, 10010, False), "baostock", "preclose 10.01"),
    "repeats_seed": ((D2, True, 10000, False), "seed", "preclose 10.00"),
    "repeats_neither": ((D2, True, 9990, False), "baostock", "preclose 9.99"),
    "step_spoils": ((D2, True, 10000, True), "baostock", "adj_factor step"),
    "no_preclose": ((D2, True, None, False), "baostock", "no exchange preclose"),
}


@pytest.mark.parametrize("reference, take, fragment", PRICE_RULES.values(), ids=PRICE_RULES)
def test_price_rules(reference, take, fragment):
    entry = proposal(conflict("price_mismatch", OTHER_CLOSE, {}, reference))
    assert (entry["fields"], entry["take"], entry["status"]) == (OHLC, take, "proposed")
    assert fragment in entry["evidence"]


AVERAGE_RULES = {  # the store's bar (None: none) -> the fields and take proposed, a fragment of the evidence
    "same_bar": ({}, ("amount",), "seed", "same bar"),
    "other_volume": ({"volume": 1100, "amount": 11000.0}, ("volume", "amount"), "seed", "volumes differ"),
    "seed_fails": ({"amount": 0.0}, ("amount",), "null", "fails the check too"),
    "seed_null_amount": ({"amount": None}, ("amount",), "null", "amount none"),
    "other_prices": ({"close": 10010, "high": 10010, "amount": 10010.0}, ("amount",), "null", "other prices"),
    "no_seed_bar": (None, ("amount",), "null", "no seed bar"),
}


@pytest.mark.parametrize("old, fields, take, fragment", AVERAGE_RULES.values(), ids=AVERAGE_RULES)
def test_average_price_rules(old, fields, take, fragment):
    entry = proposal(conflict("average_price", {"amount": 0.0}, old))
    assert (entry["kind"], entry["fields"], entry["take"]) == ("average_price", fields, take)
    assert fragment in entry["evidence"]
