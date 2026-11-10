"""screen on the synthetic market (tests.service.conftest.MARKET), expectations derived by hand from its closes.

On the last day 000002.SZ has no bar, 830799.BJ is outside the universe, 300001.SZ (35.70) is not cheap and 600000.SH
fell; 600004.SH ranks first on both factors, 000001.SZ and 600006.SH tie at 0.5 and the lower column ranks first.
"""

import pytest

from tests.frames import table_frame
from tests.service.conftest import DAYS, MARKET, SCREENED
from wequant.data.store import Store
from wequant.service import Context, Exit, ServiceError
from wequant.service.screen import ScreenRequest, screen

LAST = DAYS[-1]


def screened(home, **request):
    return screen(ScreenRequest(**{"strategy": SCREENED, **request}), Context(home))


def test_every_listed_stock_is_a_row_or_a_reject_exactly_once(market_home):
    result = screened(market_home)
    rows, rejects = [r.code for r in result.rows], [r.code for r in result.rejects]
    assert sorted(rows + rejects) == sorted(MARKET) and result.funnel[0].left == len(MARKET)
    assert [(r.code, r.step, r.kind) for r in result.rejects] == [
        ("000002.SZ", "bar", "bar"),
        ("300001.SZ", "cheap", "condition"),
        ("600000.SH", "trend", "group"),
        ("830799.BJ", "universe", "universe"),
    ]


def test_the_funnel_counts_alone_and_along_the_chain(market_home):
    got = [(s.step, s.kind, s.chain, s.passed, s.left) for s in screened(market_home).funnel]
    assert got == [
        ("listed", "listed", True, 7, 7),
        ("bar", "bar", True, 6, 6),
        ("universe", "universe", True, 5, 5),
        ("cheap", "condition", True, 4, 4),
        ("liquid", "condition", False, 5, None),
        ("rising", "condition", False, 4, None),
        ("trend", "group", True, 4, 3),
        ("scored", "scored", True, 3, 3),
        ("targets", "targets", False, 2, 2),
        ("backups", "backups", False, 1, 1),
    ]


def test_rows_by_rank_with_roles_and_capped_weights(market_home):
    result = screened(market_home)
    got = [(r.code, r.rank, r.score, r.role, r.weight, r.raw_weight, r.capped) for r in result.rows]
    assert got == [
        ("600004.SH", 1, 1.0, "target", 0.4, 0.5, True),
        ("000001.SZ", 2, 0.5, "target", 0.4, 0.5, True),
        ("600006.SH", 3, 0.5, "backup", None, None, False),
    ]
    assert result.cap_cash == pytest.approx(0.2) and result.scored_count == 3


def test_a_row_shows_its_factor_cells_and_comparison_values(market_home):
    first = screened(market_home).rows[0]  # 600004.SH: close 10.10, 9.20 three days before
    assert [c.name for c in first.factors] == ["mom", "level"]
    cells = [(c.value, c.pct, c.adjusted_pct, c.weight_share, c.contribution) for c in first.factors]
    assert cells == [pytest.approx((10.1 / 9.2, 1.0, 1.0, 0.5, 0.5)), pytest.approx((10.1, 1 / 3, 1.0, 0.5, 0.5))]
    assert first.values == pytest.approx({"cheap": 10.1, "liquid": 1_000_000})


def test_a_reject_carries_its_comparison_or_its_members(market_home):
    rejects = {r.code: r for r in screened(market_home).rejects}
    dear, fell = rejects["300001.SZ"], rejects["600000.SH"]
    assert (dear.passed, dear.value, dear.op, dear.threshold, dear.members) == (
        False,
        pytest.approx(35.7),
        "lt",
        30.0,
        None,
    )
    assert [(m.name, m.passed) for m in fell.members] == [("liquid", True), ("rising", False)] and fell.op is None


def test_the_day_defaults_to_the_stores_last_whose_next_day_is_unknown(market_home):
    last, earlier = screened(market_home), screened(market_home, date=DAYS[-2])
    assert (str(last.date), last.next_day) == (LAST, None) and str(earlier.next_day) == LAST


def test_anomalous_data_a_scored_stock_reads_is_reported_in_full(market_home):
    issue = {
        "table": "bars",
        "code": "600004.SH",
        "date": LAST,
        "kind": "price_mismatch",
        "action": "resolved_baostock",
    }
    Store(market_home).commit({}, replace={"data_issues": table_frame("data_issues", [issue], source="test")})
    result = screened(market_home)
    assert {(u.code, u.decision, u.level) for u in result.anomalies.sample} == {("600004.SH", "target", "error")}
    assert (result.anomalies.cells, result.anomalies.levels.error, result.anomalies.max_share) == (1, 1, 0.0)
    assert "data_anomaly" in [w.code for w in result.warnings]


FAILURES = [
    ({"date": "2024-01-06"}, Exit.NOT_FOUND, "not_a_trading_day", ["date"]),
    ({"strategy": {**SCREENED, "anchor": "2024-01-06"}}, Exit.USAGE, "not_a_trading_day", ["strategy", "anchor"]),
    ({"data": {"exclude_suspicious": True}}, Exit.USAGE, "requires", ["data", "exclude_suspicious"]),
    ({"strategy": None, "saved": {"id": "nope"}}, Exit.NOT_FOUND, "unknown_strategy", ["saved", "id"]),
]


@pytest.mark.parametrize("request_, exit, code, loc", FAILURES, ids=[f"{case[2]} {case[3]}" for case in FAILURES])
def test_what_screen_refuses(market_home, request_, exit, code, loc):
    with pytest.raises(ServiceError) as raised:
        screened(market_home, **request_)
    assert raised.value.exit == exit and [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_a_saved_strategy_screens_as_its_content(market_home):
    saved = screen(ScreenRequest(saved={"id": "m"}), Context(market_home))
    assert (
        saved == screened(market_home).model_copy(update={"saved_as": saved.saved_as}) and saved.saved_as[0].id == "m"
    )
