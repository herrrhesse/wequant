import polars as pl
import pytest

from tests.baostock_raw_fake import C1, D1, D2, D3, SEED, SOURCE, STEADY, World
from tests.frames import table_frame
from wequant.data.merge.conflicts import find_conflicts, next_precloses
from wequant.data.merge.propose import proposal

OHLC = ("open", "high", "low", "close")


def bars(rows: list[tuple], source: str) -> pl.DataFrame:
    """rows: (code, close, extra column values) on D1; 1000 shares, amount = volume × close."""
    full = [
        {"code": c, "date": D1, **dict.fromkeys(OHLC, close), "volume": 1000, "amount": float(close)} | extra
        for c, close, extra in rows
    ]
    return table_frame("bars", full, preclose_derived=source == SEED, source=source)


def test_find_conflicts():
    new = bars(
        [
            ("600000.SH", 10010, {"high": 10010}),  # other close
            ("600001.SH", 10000, {"amount": 0.0}),  # no amount
            ("600002.SH", 10010, {"amount": 20000.0}),  # other close and an amount above 1000 × 10.01
            ("600003.SH", 10000, {"amount": 10011.5}),  # just above the tolerance, no store bar
            ("600004.SH", 10000, {"amount": 10010.5}),  # just inside it
        ],
        SOURCE,
    )
    old = bars([(c, 10000, {}) for c in ("600000.SH", "600001.SH", "600002.SH", "600004.SH")], SEED)
    found = find_conflicts(new, old)
    assert found.select("code", "kind", "seed_close", "seed_paid", "same_volume").rows() == [
        ("600000.SH", "price_mismatch", 10000, True, True),
        ("600001.SH", "average_price", 10000, True, True),
        ("600002.SH", "average_price", 10000, True, True),
        ("600002.SH", "price_mismatch", 10000, True, True),
        ("600003.SH", "average_price", None, False, None),
    ]


def test_same_volume_allows_the_seed_s_float_rounding():
    new = bars([("600000.SH", 10000, {"amount": 0.0, "volume": 1_000_000})], SOURCE)
    old = bars([("600000.SH", 10000, {"volume": 1_000_001}), ("600001.SH", 10000, {})], SEED)
    assert find_conflicts(new, old)["same_volume"].to_list() == [True]
    old = bars([("600000.SH", 10000, {"volume": 1_000_002})], SEED)
    assert find_conflicts(new, old)["same_volume"].to_list() == [False]


@pytest.fixture
def raw_world(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    return w.write(tmp_path)


def references(store, raw, rows, adj_rows=()):
    adj = table_frame("adj_factor", [{"code": "600000.SH", "ex_date": e, "factor": f} for e, f in adj_rows])
    keys = pl.DataFrame({"code": ["600000.SH"] * len(rows), "date": rows})
    calendar = store.read_table("calendar")["date"]
    return next_precloses(raw, keys, calendar, adj).drop("code").rows()


def test_the_reference_is_the_next_day_s_exchange_preclose(raw_world):
    store, raw = raw_world
    assert references(store, raw, [D1, D3]) == [(D1, D2, 10200, False), (D3, None, None, False)]


def test_an_adj_factor_step_up_to_the_next_day_spoils_the_reference(raw_world):
    store, raw = raw_world
    assert references(store, raw, [D1], [(C1, 1.0), (D2, 1.1)]) == [(D1, D2, 10200, True)]
    assert references(store, raw, [D1], [(C1, 1.0), (D1, 1.1)]) == [(D1, D2, 10200, False)]  # in force before
    assert references(store, raw, [D1], [(C1, 1.0), (D2, 1.0)]) == [(D1, D2, 10200, False)]  # no step


def conflict(kind: str, new: dict, old: dict | None, reference=(D2, None, False)) -> dict:
    """The find_conflicts row of one bar on D1 at 10.00 (1000 shares), with a next_precloses reference."""
    stored = bars([("600000.SH", 10000, old)], SEED) if old is not None else bars([], SEED)
    row = (
        find_conflicts(bars([("600000.SH", 10000, new)], SOURCE), stored)
        .filter(pl.col("kind") == kind)
        .row(0, named=True)
    )
    return row | dict(zip(("next_date", "next_preclose", "ex_event"), reference, strict=True))


OTHER_CLOSE = {"close": 10010, "high": 10010, "amount": 10010.0}
PRICE_RULES = [
    ((D2, 10010, False), "baostock", "the 2024-01-03 exchange preclose 10.01 repeats BaoStock's"),
    ((D2, 10000, False), "seed", "the 2024-01-03 exchange preclose 10.00 repeats the seed's"),
    (
        (D2, 9990, False),
        "baostock",
        "the 2024-01-03 exchange preclose 9.99 repeats neither: BaoStock kept and the cell flagged",
    ),
    (
        (D2, 10000, True),
        "baostock",
        "the 2024-01-03 exchange preclose 10.00, but an adj_factor step by then spoils the comparison: BaoStock kept "
        "and the cell flagged",
    ),
    (
        (D2, None, False),
        "baostock",
        "no 2024-01-03 exchange preclose: BaoStock kept and the cell flagged",
    ),
]


@pytest.mark.parametrize("reference, take, verdict", PRICE_RULES)
def test_price_rules(reference, take, verdict):
    entry = proposal(conflict("price_mismatch", OTHER_CLOSE, {}, reference))
    assert (entry["fields"], entry["take"], entry["status"]) == (OHLC, take, "proposed")
    assert (
        entry["evidence"]
        == f"BaoStock close 10.01, seed close 10.00; {verdict}; also differ (BaoStock vs seed): high 10.01 vs 10.00"
    )


SEED_SPAN = "against [10.00, 10.00]"
AVERAGE_RULES = [
    (
        {},
        ("amount",),
        "seed",
        f"seed volume 1000 amount 10000.00 (average 10.0000) {SEED_SPAN}: same bar, the seed amount passes",
    ),
    (
        {"volume": 1100, "amount": 11000.0},
        ("volume", "amount"),
        "seed",
        f"seed volume 1100 amount 11000.00 (average 10.0000) {SEED_SPAN}: the volumes differ and the seed bar passes",
    ),
    (
        {"amount": 0.0},
        ("amount",),
        "null",
        f"seed volume 1000 amount 0.00 (average 0.0000) {SEED_SPAN}: the seed bar fails the check too, amount null, bar kept",
    ),
    (
        {"close": 10010, "high": 10010, "amount": 10010.0},
        ("amount",),
        "null",
        "seed volume 1000 amount 10010.00 (average 10.0100) against [10.00, 10.00], seed [10.00, 10.01]: same volume but "
        "other prices, amount null, bar kept",
    ),
]


@pytest.mark.parametrize("old, fields, take, verdict", AVERAGE_RULES)
def test_average_price_rules(old, fields, take, verdict):
    entry = proposal(conflict("average_price", {"amount": 0.0}, old))
    assert (entry["kind"], entry["fields"], entry["take"]) == ("average_price", fields, take)
    assert entry["evidence"] == f"BaoStock volume 1000 amount 0.00 (average 0.0000), {verdict}"


def test_an_average_price_row_without_a_seed_bar_voids_the_amount():
    entry = proposal(conflict("average_price", {"amount": 0.0}, None))
    assert (entry["fields"], entry["take"]) == (("amount",), "null")
    assert entry["evidence"] == (
        "BaoStock volume 1000 amount 0.00 (average 0.0000) outside [10.00, 10.00] and no seed bar: amount null, bar kept"
    )
