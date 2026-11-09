import datetime as dt
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from tests.frames import table_frame
from wequant.data.schema import TABLES
from wequant.data.store import Store
from wequant.panel import MISSING, NOT_LISTED, PRICE_FIELDS, RAW_FIELDS, SUSPENDED, TRADED, load_panel
from wequant.rules.limits import FREE, LIMITED, UNKNOWN

A, B, C, D, E = "600000.SH", "300001.SZ", "830001.BJ", "000001.SZ", "600001.SH"
DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
STATUS = {"N": NOT_LISTED, "T": TRADED, "S": SUSPENDED, "M": MISSING}
NAN = np.nan
LIMIT_ARRAYS = ("limit_up_price", "limit_down_price", "limit_known", "limit_up", "limit_down", "limit_locked")


def bar(code, day, close, pre=None, **prices):
    """A bars row on day 1..8 in 厘: open = close, high and low 5 fen away unless given."""
    row = {"open": close, "high": close + 50, "low": close - 50, "close": close, **prices}
    return {"code": code, "date": DAYS[day - 1], "volume": 100, "amount": 1000.0, "preclose": pre, **row}


def security(code, board, first, last):
    return {
        "code": code,
        "exchange": code[-2:],
        "board": board,
        "first_date": DAYS[first - 1],
        "last_date": DAYS[last - 1],
    }


def issue(code, day, kind, action, table="bars"):
    return {"table": table, "code": code, "date": DAYS[day - 1], "kind": kind, "action": action}


def status_row(code, day, tradestatus, is_st):
    return {"code": code, "date": DAYS[day - 1], "tradestatus": tradestatus, "is_st": is_st}


BARS = [
    *(bar(A, 1, 9800), bar(A, 2, 10000, 9800), bar(A, 5, 5100, 5000), bar(A, 6, 5200, 5100), bar(A, 7, 5000, 5000)),
    bar(A, 8, 5500, 5000, open=5500, high=5500, low=5500),
    *(bar(B, 3, 20000), bar(B, 4, 21000, 20000), bar(B, 6, 21500, 21000)),
    *(bar(D, 1, 10000), bar(D, 2, 10230, 10000), bar(D, 4, 10300, 10230), bar(D, 6, 10400, 10300)),
    *(bar(C, 1, 2500), bar(C, 2, 2600, 2500), bar(C, 3, 2650, 2600), bar(C, 4, 2700, 2650)),
    *(bar(C, 5, 2750, 2700), bar(C, 6, 2900, 2750), bar(C, 7, 3000, 2900)),
    bar(C, 8, 2700, 3000, open=2800, high=2800, low=2700),
]
TABLE_ROWS = {
    "calendar": [{"date": day} for day in DAYS],
    "securities": [
        security(A, "main", 1, 8),
        security(B, "chinext", 3, 6),
        security(D, "main", 1, 8),
        security(C, "bse", 1, 8),
    ],
    "bars": BARS,
    "adj_factor": [
        {"code": A, "ex_date": "2024-01-02", "factor": 1.0},
        {"code": A, "ex_date": "2024-01-06", "factor": 2.0},
        {"code": A, "ex_date": "2024-01-10", "factor": 2.08},
    ],
    "index_bars": [
        {
            "code": "000300.SH",
            "date": DAYS[t],
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 3500000 + 1000 * t,
            "volume": 1,
            "amount": 1.0,
        }
        for t in range(8)
        if t != 3
    ],
    "data_issues": [
        issue(A, 3, "off_scale", "row_dropped"),
        issue(B, 7, "bar_unconfirmed", "row_dropped"),
        issue(D, 7, "bar_unconfirmed", "row_dropped"),
        issue("600002.SH", 3, "off_scale", "row_dropped"),  # a code the grid does not have
        issue(A, 4, "duplicate_code", "duplicate_dropped"),
        issue(B, 1, "before_first_bar", "status_dropped", table="daily_status"),
        issue(B, 8, "limit_breach", "limits_voided"),
    ],
}


def frame(name, rows):
    """A store frame of name from row dicts, with source and preclose_derived filled in."""
    constants = {"source": "test"} if "source" in TABLES[name].schema else {}
    if name == "bars":
        constants["preclose_derived"] = True
    return table_frame(name, rows, **constants)


def commit(store, **tables):
    """Replace each named table with frame(name, rows); a new store version."""
    return store.commit({"source": "test"}, replace={name: frame(name, rows) for name, rows in tables.items()})


def seed(home, **changes):
    """A store at version 1 holding TABLE_ROWS, with changed tables' rows replaced."""
    store = Store(home)
    commit(store, **{**TABLE_ROWS, **changes})
    return store


def half_up(numerator, denominator):
    return (numerator + denominator // 2) // denominator


def stub_limits(store, manifest=None, book=None):
    """10% limits from each bar's preclose: FREE for B on its listing days 1-5, UNKNOWN without a preclose."""
    bars = store.scan("bars", columns=["code", "date", "preclose"], manifest=manifest).collect()
    pre = pl.col("preclose").cast(pl.Int64)
    free = (pl.col("code") == B) & pl.col("date").is_between(dt.date(2024, 1, 4), dt.date(2024, 1, 10))
    state = pl.when(free).then(FREE).when(pre.is_null()).then(UNKNOWN).otherwise(LIMITED).cast(pl.Int8)
    limited = pl.col("limit_state") == LIMITED
    return (
        bars.with_columns(limit_state=state)
        .select(
            "code",
            "date",
            limit_up=pl.when(limited).then(half_up(pre * 11000, 100_000) * 10).cast(pl.Int32),
            limit_down=pl.when(limited).then(half_up(pre * 9000, 100_000) * 10).cast(pl.Int32),
            limit_state="limit_state",
        )
        .sort("code", "date")
    )


@pytest.fixture
def store(tmp_path):
    return seed(tmp_path / "home")


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


def install_stub_rules(monkeypatch):
    """Scan-based limits and a stub rule book; returns the versions load_limits was called for."""
    calls = []

    def recorded(store, manifest=None, book=None):
        calls.append(manifest["version"])
        return stub_limits(store, manifest, book)

    monkeypatch.setattr("wequant.panel.build.load_limits", recorded)
    monkeypatch.setattr("wequant.panel.panel.default_book", lambda: SimpleNamespace(digest="stub" * 16))
    return calls


@pytest.fixture
def stub_rules(monkeypatch):
    return install_stub_rules(monkeypatch)


def col(panel, code):
    return list(panel.codes).index(code)


def statuses(letters):
    return [STATUS[x] for x in letters.split()]


def assert_invariants(panel, store):
    """listed is the disjoint union of traded, suspended and missing; bar fields are NaN exactly off bars."""
    traded, suspended, missing = panel.traded, panel.suspended, panel.missing
    assert np.array_equal(panel.listed, traded | suspended | missing)
    assert not (traded & suspended).any() and not (traded & missing).any() and not (suspended & missing).any()
    for field in RAW_FIELDS:
        if field != "preclose":
            assert np.array_equal(np.isnan(panel.raw(field)), ~traded)
    no_pre = np.isnan(panel.raw("preclose"))
    assert (no_pre | traded).all()
    nulls = store.scan("bars", columns=["code", "date", "preclose"], version=panel.version).filter(
        pl.col("preclose").is_null()
    )
    expected = np.zeros_like(traded)
    for code, day in nulls.select("code", "date").collect().rows():
        if code in panel.codes and np.datetime64(day) in panel.dates:
            expected[panel.row(day), col(panel, code)] = True
    assert np.array_equal(no_pre & traded, expected)


# ---------------------------------------------------------------- axes, units and goldens


def test_axes_and_units(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    assert panel.version == 1 and panel.key.startswith("f1-v1-")
    assert panel.dates.tolist() == [dt.date.fromisoformat(d) for d in DAYS] and panel.dates.dtype == "datetime64[D]"
    assert panel.codes.tolist() == [D, B, A, C] and panel.codes.dtype == "<U9"
    assert panel.boards.tolist() == ["main", "chinext", "main", "bse"]
    assert panel.exchanges.tolist() == ["SZ", "SZ", "SH", "BJ"]
    assert panel.raw("close")[1, col(panel, D)] == 10.23
    assert panel.raw("volume")[1, col(panel, D)] == 100.0 and panel.raw("amount")[1, col(panel, D)] == 1000.0
    assert panel.raw("close").shape == (8, 4) and panel.raw("close").dtype == np.float64


def test_golden_a(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    a = col(panel, A)
    assert panel.status[:, a].tolist() == statuses("T T M S T T T T")
    np.testing.assert_array_equal(panel.raw("close")[:, a], [9.80, 10.00, NAN, NAN, 5.10, 5.20, 5.00, 5.50])
    np.testing.assert_allclose(panel.factor[:, a], [1, 1, 1, 1, 2, 2, 2.08, 2.08])
    adjusted = panel.adjusted("close")[:, a]
    np.testing.assert_allclose(adjusted, [9.80, 10.00, NAN, NAN, 10.20, 10.40, 10.40, 11.44])
    pre = panel.adjusted("preclose")[:, a]
    assert pre[4] == adjusted[1] == 10.0
    assert pre[6] == pytest.approx(adjusted[5]) and adjusted[5] == pytest.approx(10.40)
    assert np.isnan(pre[0]) and panel.traded[0, a]


def test_golden_b(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    assert panel.status[:, col(panel, B)].tolist() == statuses("N N T T S T M N")


def test_golden_d_without_daily_status(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    assert panel.status[:, col(panel, D)].tolist() == statuses("T T S T S T M S")
    assert not panel.st.any() and not panel.st_known.any()
    assert (panel.status[:, col(panel, C)] == TRADED).all()


def test_golden_d_with_daily_status(store, cache):
    rows = [
        status_row(D, t + 1, ts, st) for t, (ts, st) in enumerate(zip([1, 1, 0, 1, 1, 0, 1], [0, 0, 0, 1, 1, 1, 1]))
    ]
    spans = [s if s["code"] != D else security(D, "main", 1, 6) for s in TABLE_ROWS["securities"]]
    commit(store, daily_status=rows, securities=spans)
    panel = load_panel(store=store, cache_dir=cache)
    d = col(panel, D)
    assert panel.version == 2
    assert panel.status[:, d].tolist() == statuses("T T S T M T M N")
    assert panel.st[:, d].tolist() == [False, False, False, True, True, True, True, False]
    assert panel.st_known[:, d].tolist() == [True] * 7 + [False]
    assert (panel.status[:, col(panel, C)] == TRADED).all() and not panel.st_known[:, col(panel, C)].any()
    assert_invariants(panel, store)


def test_only_row_dropped_on_bars_makes_a_day_missing(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    assert panel.status[3, col(panel, A)] == SUSPENDED  # duplicate_dropped
    assert panel.status[0, col(panel, B)] == NOT_LISTED  # status_dropped
    assert panel.status[7, col(panel, B)] == NOT_LISTED  # limits_voided


def test_invariants_on_every_slice(store, cache):
    for query in [{}, {"start": "2024-01-04", "end": "2024-01-09"}, {"codes": [C, A]}, {"start": "2024-01-06"}]:
        assert_invariants(load_panel(store=store, cache_dir=cache, **query), store)


def test_on_bars_bridges_a_mask_to_a_condition(store, cache):
    panel = load_panel(end="2024-01-05", codes=[D], store=store, cache_dir=cache)
    assert panel.traded[:, 0].tolist() == [True, True, False, True]
    mask = np.array([[True], [False], [True], [True]])
    result = panel.on_bars(mask)
    assert result.dtype == np.float64
    np.testing.assert_array_equal(result[:, 0], [1, 0, NAN, 1])
    np.testing.assert_array_equal(panel.on_bars(mask.astype(float))[:, 0], [1, 0, NAN, 1])
    with pytest.raises(ValueError, match="shape"):
        panel.on_bars(mask[:3])


def test_adjusted_is_for_prices_only(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    with pytest.raises(ValueError):
        panel.adjusted("volume")
    with pytest.raises(ValueError):
        panel.raw("turnover")


# ---------------------------------------------------------------- limits


def test_limits_from_load_limits(store, cache, stub_rules):
    panel = load_panel(store=store, cache_dir=cache)
    a, b, c = col(panel, A), col(panel, B), col(panel, C)
    up, down = panel.limit_up_price, panel.limit_down_price
    assert (up[7, a], down[7, a]) == (5.50, 4.50)
    assert panel.limit_up[7, a] and panel.limit_locked[7, a] and not panel.limit_down[7, a]
    assert panel.limit_known[6, a] and not (panel.limit_up[6, a] or panel.limit_down[6, a] or panel.limit_locked[6, a])
    assert np.isnan(up[0, a]) and np.isnan(down[0, a]) and not panel.limit_known[0, a]
    assert not (panel.limit_up[0, a] or panel.limit_down[0, a] or panel.limit_locked[0, a])
    for t in (2, 3, 5):
        assert (up[t, b], down[t, b]) == (np.inf, -np.inf) and panel.limit_known[t, b] and not panel.limit_up[t, b]
    assert np.isnan(up[[4, 6], b]).all() and np.isnan(down[[4, 6], b]).all()
    assert panel.raw("close")[6, c] == 3.00 and down[7, c] == 2.70
    assert panel.limit_down[7, c] and not panel.limit_locked[7, c]
    assert np.array_equal(panel.limit_known, (panel.traded & ~np.isnan(panel.raw("preclose"))) | (up == np.inf))
    assert panel.rules_key == "stubstubstub-r1"
    assert (cache / "panel" / panel.key / "limits-stubstubstub-r1.npy").exists()


def test_limits_with_the_real_rules(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    a, b = col(panel, A), col(panel, B)
    assert (panel.limit_up_price[7, a], panel.limit_down_price[7, a]) == (5.50, 4.50)
    for t in (2, 3, 5):
        assert (panel.limit_up_price[t, b], panel.limit_down_price[t, b]) == (np.inf, -np.inf)


# ---------------------------------------------------------------- slicing and read-only


def test_slicing(store, cache, stub_rules):
    full = load_panel(store=store, cache_dir=cache)
    part = load_panel("2024-01-04", "2024-01-09", store=store, cache_dir=cache)
    assert part.dates.tolist() == [dt.date.fromisoformat(d) for d in DAYS[2:6]]
    for field in RAW_FIELDS:
        assert np.array_equal(part.raw(field), full.raw(field)[2:6], equal_nan=True)
        if field in PRICE_FIELDS:
            assert np.array_equal(part.adjusted(field), full.adjusted(field)[2:6], equal_nan=True)
    for name in ("factor", "status", "listed", "traded", "suspended", "missing", "st", "st_known", *LIMIT_ARRAYS):
        assert np.array_equal(getattr(part, name), getattr(full, name)[2:6], equal_nan=name.endswith("price"))
    weekend = load_panel("2024-01-06", "2024-01-13", store=store, cache_dir=cache)
    assert weekend.dates.tolist() == [dt.date.fromisoformat(d) for d in DAYS[4:]]
    early = load_panel(dt.date(2023, 12, 30), "2024-01-07", store=store, cache_dir=cache)
    assert early.dates.tolist() == [dt.date.fromisoformat(d) for d in DAYS[:4]]
    picked = load_panel(codes=[C, A], store=store, cache_dir=cache)
    assert picked.codes.tolist() == [C, A] and picked.boards.tolist() == ["bse", "main"]
    assert np.array_equal(picked.raw("close"), full.raw("close")[:, [3, 2]], equal_nan=True)
    for name in LIMIT_ARRAYS:
        assert np.array_equal(getattr(picked, name), getattr(full, name)[:, [3, 2]], equal_nan=name.endswith("price"))
    assert full.row("2024-01-08") == 4 and full.row(np.datetime64("2024-01-08")) == 4
    with pytest.raises(KeyError):
        full.row("2024-01-06")
    with pytest.raises(KeyError):
        load_panel(codes=[A, "600009.SH"], store=store, cache_dir=cache)
    with pytest.raises(ValueError):
        load_panel(codes=[A, A], store=store, cache_dir=cache)
    with pytest.raises(ValueError):
        load_panel("2024-01-06", "2024-01-07", store=store, cache_dir=cache)


def test_arrays_are_read_only(store, cache, stub_rules):
    for panel in (load_panel(store=store, cache_dir=cache), load_panel(codes=[A], store=store, cache_dir=cache)):
        arrays = [panel.raw("close"), panel.adjusted("close"), panel.traded, panel.limit_up, panel.limit_up_price]
        arrays += [
            panel.on_bars(panel.st),
            panel.factor,
            panel.status,
            panel.dates,
            panel.codes,
            panel.index_close("000300.SH"),
        ]
        for array in arrays:
            with pytest.raises(ValueError):
                array[0] = 1
    rows = load_panel("2024-01-04", "2024-01-09", store=store, cache_dir=cache).raw("close")
    assert not rows.flags.owndata and not rows.flags.writeable  # a view into the memory map


def test_nothing_committed_raises_lookup_error(tmp_path):
    with pytest.raises(LookupError):
        load_panel(store=Store(tmp_path / "empty"), cache_dir=tmp_path / "cache")


# ---------------------------------------------------------------- point in time


def test_later_versions_do_not_change_earlier_rows(tmp_path, stub_rules):
    store = seed(
        tmp_path / "home",
        securities=[*TABLE_ROWS["securities"], security(E, "main", 1, 3)],
        bars=[*BARS, bar(E, 1, 4000), bar(E, 2, 4100, 4000), bar(E, 3, 4200, 4100)],
    )
    before = load_panel(store=store, cache_dir=tmp_path / "cache")
    spans = {s["code"]: s for s in TABLE_ROWS["securities"]} | {
        B: security(B, "chinext", 3, 8),
        E: security(E, "main", 1, 8),
    }
    commit(
        store,
        adj_factor=[*TABLE_ROWS["adj_factor"], {"code": A, "ex_date": DAYS[7], "factor": 2.5}],
        bars=[
            *BARS,
            bar(E, 1, 4000),
            bar(E, 2, 4100, 4000),
            bar(E, 3, 4200, 4100),
            bar(B, 8, 22000, 21500),
            bar(E, 8, 4300, 4200),
        ],
        daily_status=[status_row(D, 8, 1, True)],
        securities=list(spans.values()),
    )
    after = load_panel(store=store, cache_dir=tmp_path / "cache")
    assert (before.version, after.version) == (1, 2) and before.codes.tolist() == after.codes.tolist()
    grids = [*(f"raw:{f}" for f in RAW_FIELDS), *(f"adjusted:{f}" for f in PRICE_FIELDS)]
    grids += ["factor", "traded", "st", "st_known", *LIMIT_ARRAYS]
    for name in grids:
        old, new = (_grid(p, name) for p in (before, after))
        assert np.array_equal(old[:7], new[:7], equal_nan=True), name
    assert not np.array_equal(_grid(before, "factor")[7], _grid(after, "factor")[7])
    e = col(after, E)
    # The documented exception: a later bar turns days after E's last bar from NOT_LISTED into SUSPENDED.
    assert before.status[3:7, e].tolist() == [NOT_LISTED] * 4
    assert after.status[3:7, e].tolist() == [SUSPENDED] * 4
    others = np.ones(len(after.codes), dtype=bool)
    others[e] = False
    assert np.array_equal(before.status[:7, others], after.status[:7, others])
    assert_invariants(before, store)
    assert_invariants(after, store)


def _grid(panel, name):
    kind, _, field = name.partition(":")
    return getattr(panel, kind)(field) if field else getattr(panel, name)


def test_index_close_leaves_missing_days_empty(store, cache):
    panel = load_panel(store=store, cache_dir=cache)
    close = panel.index_close("000300.SH")
    np.testing.assert_array_equal(close, [3500, 3501, 3502, NAN, 3504, 3505, 3506, 3507])
    part = load_panel("2024-01-05", "2024-01-08", store=store, cache_dir=cache)
    np.testing.assert_array_equal(part.index_close("000300.SH"), [NAN, 3504])
