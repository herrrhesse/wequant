import polars as pl
import pytest

from tests.baostock_raw_fake import C1, D1, D2, D3, SEED, SOURCE, STEADY, World, raw_row
from tests.frames import table_frame
from wequant.data.merge import RawDataError
from wequant.data.merge.conflicts import amount_conflicts, price_conflicts, with_references
from wequant.data.merge.propose import proposal

OHLC = ("open", "high", "low", "close")
MARKS = ("stale", "truncated", "carried")


def bars(rows: list[tuple], source: str) -> pl.DataFrame:
    """rows: (code, close, extra column values) on D1; 1000 shares, amount = volume × close."""
    full = [
        {"code": c, "date": D1, **dict.fromkeys(OHLC, close), "volume": 1000, "amount": float(close)} | extra
        for c, close, extra in rows
    ]
    return table_frame("bars", full, preclose_derived=source == SEED, source=source)


def unmarked(bars: pl.DataFrame, **marked: str) -> pl.DataFrame:
    """detect.marks of the bars: every mark False, except the code each named mark is set on."""
    keys = bars.select("code", "date")
    return keys.with_columns((pl.col("code") == marked[m] if m in marked else pl.lit(False)).alias(m) for m in MARKS)


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
    assert amount_conflicts(new, old, unmarked(new)).select(columns).rows() == [
        ("600001.SH", "average_price", 10000, True, True),
        ("600002.SH", "average_price", 10000, True, True),
        ("600003.SH", "average_price", None, False, None),
        # same_volume allows the seed's float rounding (VOLUME_RTOL 1e-6); 10,000 yuan for a million shares fails
        ("600005.SH", "average_price", 10000, False, True),
        ("600006.SH", "average_price", 10000, False, False),
        ("600007.SH", "average_price", 10000, False, True),  # a null seed amount fails the check
    ]


def test_a_preclose_off_the_fen_is_a_price_conflict():
    new = bars([("600000.SH", 10000, {"preclose": 9968}), ("600001.SH", 10000, {"preclose": 9970})], SOURCE)
    assert price_conflicts(new, bars([], SEED)).select("code", "kind").rows() == [("600000.SH", "preclose_odd")]


AMOUNT_KINDS = {  # the marks on 600000.SH and its seed amount -> the one kind it gets, the first that applies
    "stale_first": ({"stale", "truncated", "carried"}, 5000.0, "stale_volume"),
    "truncated_before_carried": ({"truncated", "carried"}, 5000.0, "volume_truncated"),
    "carried": ({"carried"}, 10000.0, "amount_carried"),
    "carried_but_the_seed_agrees": ({"carried"}, 7000.0, None),
    "unmarked": (set(), 10000.0, None),
}


@pytest.mark.parametrize("marked, seed_amount, kind", AMOUNT_KINDS.values(), ids=AMOUNT_KINDS)
def test_each_bar_gets_the_first_amount_kind_that_applies(marked, seed_amount, kind):
    new = bars([("600000.SH", 7000, {"amount": 7000.0})], SOURCE)  # paid: 1000 shares at 7.00
    old = bars([("600000.SH", 7000, {"amount": seed_amount})], SEED)
    found = amount_conflicts(new, old, unmarked(new, **dict.fromkeys(marked, "600000.SH")))
    assert found["kind"].to_list() == ([kind] if kind else [])


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


def test_a_reference_preclose_finer_than_the_li_raises(tmp_path):
    store, raw = write_world(tmp_path, preclose="10.2055")
    with pytest.raises(RawDataError, match=r"reference preclose: 1 rows, e.g. \[\('600000.SH'"):
        references(store, raw, [D1])
    assert references(store, raw, [D2]) == [(D2, D3, True, 10300, False)]  # D3's row is not D2's
    store, raw = write_world(tmp_path / "li", preclose="10.2050")
    assert references(store, raw, [D1]) == [(D1, D2, True, 10205, False)]  # an exchange preclose may be whole 厘


def conflict(kind: str, new: dict, old: dict | None, reference=(D2, True, None, False)) -> dict:
    """The conflict row of one bar on D1 at 10.00 (1000 shares), with a reference (next_date, fetched, preclose,
    ex_event)."""
    stored = bars([("600000.SH", 10000, old)], SEED) if old is not None else bars([], SEED)
    new = bars([("600000.SH", 10000, new)], SOURCE)
    found = price_conflicts(new, stored) if kind == "price_mismatch" else amount_conflicts(new, stored, unmarked(new))
    row = found.row(0, named=True)
    return row | dict(zip(("next_date", "fetched", "next_preclose", "ex_event"), reference, strict=True))


OTHER_CLOSE = {"close": 10010, "high": 10010, "amount": 10010.0}
PRICE_RULES = {  # the next day's exchange preclose (None: no row) and an adj_factor step by then -> take, a fragment
    "repeats_baostock": ((D2, True, 10010, False), "baostock", "preclose 10.01"),
    "repeats_seed": ((D2, True, 10000, False), "seed", "preclose 10.00"),
    "repeats_neither": ((D2, True, 9990, False), "undetermined", "preclose 9.99"),
    "step_spoils": ((D2, True, 10000, True), "undetermined", "adj_factor step"),
    "no_preclose": ((D2, True, None, False), "undetermined", "no exchange preclose"),
}


@pytest.mark.parametrize("reference, take, fragment", PRICE_RULES.values(), ids=PRICE_RULES)
def test_price_rules(reference, take, fragment):
    entry = proposal(conflict("price_mismatch", OTHER_CLOSE, {}, reference))
    assert (entry["fields"], entry["take"], entry["status"]) == (OHLC, take, "proposed")
    assert fragment in entry["evidence"]


def test_equal_closes_leave_a_price_conflict_undetermined():
    """The next day's preclose repeats both closes, so it cannot tell which open, high or low is right."""
    entry = proposal(conflict("price_mismatch", {"high": 10010}, {}, (D2, True, 10000, False)))
    assert entry["take"] == "undetermined" and "equal closes" in entry["evidence"]


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


def marked(mark: str, new: dict, old: dict | None) -> dict:
    """The amount conflict of one bar on D1 at 10.00 (1000 shares) carrying one mark."""
    stored = bars([("600000.SH", 10000, old)], SEED) if old is not None else bars([], SEED)
    fresh = bars([("600000.SH", 10000, new)], SOURCE)
    return amount_conflicts(fresh, stored, unmarked(fresh, **{mark: "600000.SH"})).row(0, named=True)


TRADED = ("volume", "amount")
MARK_RULES = {  # mark, BaoStock's bar, the store's bar (None: none) -> the fields and take proposed
    "stale_builds_no_bar": ("stale", {"amount": 0.0}, None, (), "no_bar"),
    "truncated_takes_the_seed": ("truncated", {"volume": 800, "amount": 8000.0}, {}, TRADED, "seed"),
    "truncated_without_a_seed_amount": ("truncated", {"volume": 800}, {"amount": None}, TRADED, "undetermined"),
    "carried_takes_the_seed": ("carried", {"amount": 9500.0}, {}, ("amount",), "seed"),
    "carried_without_a_seed_bar": ("carried", {"amount": 9500.0}, None, ("amount",), "undetermined"),
    "carried_at_another_seed_volume": ("carried", {"amount": 9500.0}, {"volume": 1100}, ("amount",), "undetermined"),
}


@pytest.mark.parametrize("mark, new, old, fields, take", MARK_RULES.values(), ids=MARK_RULES)
def test_detected_conflict_rules(mark, new, old, fields, take):
    entry = proposal(marked(mark, new, old))
    assert (entry["fields"], entry["take"], entry["status"]) == (fields, take, "proposed")


def test_a_preclose_off_the_fen_is_proposed_undetermined():
    """Only the exchange can say whether such a preclose is right."""
    row = price_conflicts(bars([("600000.SH", 10000, {"preclose": 9968})], SOURCE), bars([], SEED)).row(0, named=True)
    entry = proposal(row | dict.fromkeys(("next_date", "fetched", "next_preclose", "ex_event")))
    assert (entry["kind"], entry["fields"], entry["take"]) == ("preclose_odd", ("preclose",), "undetermined")
