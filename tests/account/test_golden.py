"""The golden run: three stocks, 20 days and 19 orders through the store, the panel and the real rule book."""

import math

import numpy as np
import polars as pl
import pytest

from tests.account.fake import FakePanel
from tests.account.golden import DAYS, A, B, C, build_store
from wequant.account import ORDER_REASONS, REJECTED, Account, Execution
from wequant.analytics import compute_metrics
from wequant.panel import load_panel

INF = math.inf
BATCHES = {  # day d: calls, each a list of (code, qty)
    2: [[(A, 10000), (B, 300), (C, 150), (C, 250)]],
    5: [[(A, 1000)]],
    6: [[(C, -INF)]],
    7: [[(B, -INF)]],
    8: [[(B, -INF), (A, 100)]],
    9: [[(C, -100), (C, -INF)]],
    10: [[(A, -5050)]],
    11: [[(B, 1000)], [(B, -INF)]],
    12: [[(B, -INF)]],
    13: [[(C, 30000)]],
    14: [[(B, 100)]],
    15: [[(A, -INF)]],
    16: [[(A, -INF)]],
}
NO = (0, 0.0, 0, 0, 0)  # shares, value, commission, stamp tax, transfer fee of a rejected order
ORDERS = [  # day, code, side, requested, status, price, (shares, value, commission, stamp tax, transfer fee), reason
    (2, A, "buy", 10000, "filled", 10.00, (10000, 100_000.00, 30.00, 0, 1.00), None),
    (2, B, "buy", 300, "filled", 8.00, (300, 2_400.00, 5.00, 0, 0.02), None),
    (2, C, "buy", 150, "rejected", 50.00, NO, "lot"),
    (2, C, "buy", 250, "filled", 50.00, (250, 12_500.00, 5.00, 0, 0.13), None),
    (5, A, "buy", 1000, "rejected", 11.55, NO, "limit_up"),
    (6, C, "sell", INF, "rejected", None, NO, "no_bar"),
    (7, B, "sell", INF, "rejected", 7.38, NO, "limit_down"),
    (8, B, "sell", INF, "filled", 7.00, (300, 2_100.00, 5.00, 1.05, 0.02), None),
    (8, A, "buy", 100, "filled", 7.90, (100, 790.00, 5.00, 0, 0.01), None),
    (9, C, "sell", 100, "rejected", 41.60, NO, "lot"),
    (9, C, "sell", INF, "filled", 41.60, (312.5, 13_000.00, 5.00, 6.49, 0.13), None),
    (10, A, "sell", 5050, "filled", 8.70, (5000, 43_500.00, 13.05, 21.75, 0.44), None),
    (11, B, "buy", 1000, "filled", 7.20, (1000, 7_200.00, 5.00, 0, 0.07), None),
    (11, B, "sell", INF, "rejected", 7.20, NO, "t1"),
    (12, B, "sell", INF, "filled", 7.30, (1000, 7_300.00, 5.00, 3.65, 0.07), None),
    (13, C, "buy", 30000, "partial", 42.40, (22231, 942_594.40, 282.78, 0, 9.43), "cash"),
    (14, B, "buy", 100, "rejected", 7.30, NO, "limit_unknown"),
    (15, A, "sell", INF, "filled", 9.20, (10100, 92_920.00, 27.88, 46.46, 0.93), None),
    (16, A, "sell", INF, "rejected", 9.30, NO, "no_position"),
]
DAILY = [  # equity, cash, bought, sold for d1..d20
    (1_000_000.00, 1_000_000.00, 0, 0),
    (1_001_113.85, 885_058.85, 114_900, 0),
    (1_003_238.85, 885_058.85, 0, 0),
    (1_005_268.85, 885_058.85, 0, 0),
    (1_015_768.85, 885_058.85, 0, 0),
    (1_017_268.85, 885_058.85, 0, 0),
    (1_017_022.85, 885_058.85, 0, 0),
    (1_028_884.52, 886_357.77, 790, 2_100),
    (1_030_716.15, 899_346.15, 0, 13_000),
    (1_031_690.91, 942_810.91, 0, 43_500),
    (1_032_695.84, 935_605.84, 7_200, 0),
    (1_033_797.12, 942_897.12, 0, 7_300),
    (1_038_961.11, 10.51, 942_594.40, 0),
    (1_044_417.31, 10.51, 0, 0),
    (1_048_788.24, 92_855.24, 0, 92_920),
    (1_053_234.44, 92_855.24, 0, 0),
    (1_037_672.74, 92_855.24, 0, 0),
    (1_042_396.8275, 92_855.24, 0, 0),
    (1_047_120.915, 92_855.24, 0, 0),
    (1_051_845.0025, 92_855.24, 0, 0),
]
POSITIONS = {  # day d: {code: (shares, value)}
    2: {A: (10000, 101_000), B: (300, 2_430), C: (250, 12_625)},
    3: {A: (10000, 103_000), B: (300, 2_430), C: (250, 12_750)},
    4: {A: (10000, 105_000), B: (300, 2_460), C: (250, 12_750)},
    5: {A: (10000, 115_500), B: (300, 2_460), C: (312.5, 12_750)},
    6: {A: (15000, 117_000), B: (300, 2_460), C: (312.5, 12_750)},
    7: {A: (15000, 117_000), B: (300, 2_214), C: (312.5, 12_750)},
    8: {A: (15100, 129_558), C: (312.5, 12_968.75)},
    9: {A: (15100, 131_370)},
    10: {A: (10100, 88_880)},
    11: {A: (10100, 89_890), B: (1000, 7_200)},
    12: {A: (10100, 90_900)},
    13: {A: (10100, 91_910), C: (22231, 947_040.60)},
    14: {A: (10100, 92_920), C: (22231, 951_486.80)},
    15: {C: (22231, 955_933)},
    16: {C: (22231, 960_379.20)},
    17: {C: (22231, 944_817.50)},
    18: {C: (23620.4375, 949_541.5875)},
    19: {C: (23620.4375, 954_265.675)},
    20: {C: (23620.4375, 958_989.7625)},
}


def li(x) -> int:
    return round(x * 1000)


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("golden")
    return load_panel(store=build_store(home / "home"), cache_dir=home / "cache")


@pytest.fixture(scope="module")
def ledger(panel):
    account = Account(panel, 1_000_000, DAYS[0], Execution(slippage_bps=0))  # the costs below leave slippage out
    column = {code: i for i, code in enumerate(panel.codes.tolist())}
    for t in range(1, len(DAYS)):
        account.begin(t)
        for call in BATCHES.get(t + 1, []):
            account.trade([column[code] for code, _ in call], [qty for _, qty in call])
        account.end()
    return account.ledger()


def cell(panel, name, code, day):
    return getattr(panel, name)[day - 1, panel.codes.tolist().index(code)]


def test_panel_facts(panel):
    assert cell(panel, "limit_up_price", A, 5) == 11.55 and cell(panel, "limit_up_price", A, 8) == 8.58
    assert cell(panel, "limit_up", A, 8)
    assert cell(panel, "limit_down_price", B, 7) == 7.38 and cell(panel, "limit_down_price", B, 8) == 6.64
    assert not cell(panel, "limit_known", B, 14)
    assert not any(cell(panel, "traded", C, d) for d in range(4, 8))
    assert cell(panel, "factor", C, 4) == 1.0 and cell(panel, "factor", C, 5) == 1.25


def test_orders(ledger):
    orders = ledger.orders
    assert orders.height == len(ORDERS)
    for row, (day, code, side, requested, status, price, money, reason) in zip(orders.iter_rows(named=True), ORDERS):
        shares, value, commission, stamp, transfer = money
        assert (row["date"].isoformat(), row["code"], row["side"]) == (DAYS[day - 1], code, side)
        assert (row["requested"], row["status"], row["reason"], row["shares"]) == (requested, status, reason, shares)
        assert row["price"] == price
        got = [li(row[name]) for name in ("value", "commission", "stamp_tax", "transfer_fee", "slippage")]
        assert got == [li(value), li(commission), li(stamp), li(transfer), 0]
        assert row["day_amount"] == (None if price is None else 1e7)  # every golden bar trades 1e7 yuan


def test_daily(ledger):
    daily = ledger.daily
    assert daily["date"].cast(pl.String).to_list() == DAYS
    for row, (equity, cash, bought, sold) in zip(daily.iter_rows(named=True), DAILY):
        assert row["equity"] == pytest.approx(equity, rel=1e-12, abs=0)
        assert (li(row["cash"]), li(row["bought"]), li(row["sold"])) == (li(cash), li(bought), li(sold))


def test_positions(ledger):
    rows = ledger.positions.iter_rows(named=True)
    got = {}
    for row in rows:
        got.setdefault(DAYS.index(row["date"].isoformat()) + 1, {})[row["code"]] = (row["shares"], row["value"])
    assert got.keys() == POSITIONS.keys() and ledger.positions.height == 35
    for day, held in POSITIONS.items():
        assert got[day].keys() == held.keys()
        for code, (shares, value) in held.items():
            assert got[day][code][0] == shares
            assert got[day][code][1] == pytest.approx(value, rel=1e-12, abs=0)


def test_equity_is_cash_plus_positions(ledger):
    values = ledger.positions.group_by("date").agg(pl.col("value").sum())
    days = ledger.daily.join(values, on="date", how="left").with_columns(pl.col("value").fill_null(0.0))
    assert np.allclose(days["equity"], days["cash"] + days["value"], rtol=0, atol=1e-9)


def test_metrics(panel, ledger):
    m = compute_metrics(ledger.daily, pl.Series("date", panel.dates), positions=ledger.positions, orders=ledger.orders)
    assert m["total_return"].value == pytest.approx(0.0518450025, rel=1e-12)
    assert m["max_drawdown"].value == pytest.approx(0.0147751530039220, rel=1e-12)
    assert m["max_drawdown_peak"].value.isoformat() == DAYS[15]
    assert m["max_drawdown_trough"].value.isoformat() == DAYS[16]
    assert m["turnover"].value == pytest.approx(7.561705320347529, rel=1e-12)
    assert m["avg_holding_days"].value == 7.0
    assert m["executable_order_share"].value == pytest.approx(11 / 19, rel=1e-15)
    assert m["capacity_max"].value == pytest.approx(942_594.40 / 1e7, rel=1e-12)
    # 11 fills; the 95th percentile sits halfway between the 10th (100,000) and the 11th (942,594.40) value
    assert m["capacity_p95"].value == pytest.approx(521_297.20 / 1e7, rel=1e-12)
    assert m["capacity_unknown_fills"].value == 0


def test_close_model_agrees_with_the_limit_mask(panel):
    account = Account(panel, 1_000_000, DAYS[0], Execution(price="close"))
    column = panel.codes.tolist().index(A)
    for t in range(1, 8):
        account.begin(t)
        if t == 7:
            got = account.trade([column], [100])
            assert (got.status[0], ORDER_REASONS[got.reason[0]], got.price[0]) == (REJECTED, "limit_up", 8.58)
        account.end()
    assert account.cash == 1_000_000


def test_fake_panel_has_only_real_panel_attributes(panel):
    fake = FakePanel(DAYS[:2], [A], ["main"], [10.0, 10.0], [10.0, 10.0])
    assert all(hasattr(panel, name) for name in dir(fake) if not name.startswith("_"))
