import datetime as dt
import json
import math

import numpy as np
import polars as pl
import pytest

from wequant.analytics import METRICS_VERSION, Metric, MetricSpec, compute_metrics, metrics_json
from wequant.analytics.inputs import ORDER_STATUSES, ORDERS, POSITIONS

KEYS = [
    "trading_days",
    "total_return",
    "annual_return",
    "annual_volatility",
    "sharpe",
    "annual_return_ex_best",
    "max_drawdown",
    "max_drawdown_peak",
    "max_drawdown_trough",
    "max_drawdown_recovery",
    "max_drawdown_days",
    "max_days_without_new_high",
    "win_rate_daily",
    "win_rate_weekly",
    "win_rate_monthly",
    "excess_win_rate_daily",
    "excess_win_rate_weekly",
    "excess_win_rate_monthly",
    "benchmark_missing_days",
    "tracking_error",
    "information_ratio",
    "turnover",
    "avg_holding_days",
    "executable_order_share",
]
SPEC = MetricSpec(periods_per_year=4, risk_free_rate=0.0, tail_fraction=0.25)
GOLDEN_DAYS = ["2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05", "2024-03-01"]
GOLDEN_EQUITY = [100, 110, 99, 99, 118.8]
GOLDEN_BENCHMARK = [100, 105, 92.4, 93.324, 102.6564]


def day(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso)


def weekdays(count: int) -> list[str]:
    """count consecutive weekdays from Tuesday 2024-01-02."""
    days = (dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(2 * count + 2))
    return [d.isoformat() for d in days if d.weekday() < 5][:count]


def daily(days: list[str], equity: list, **columns: list) -> pl.DataFrame:
    """A daily frame from ISO days and Float64 columns; None is null."""
    floats = {name: pl.Series(values, dtype=pl.Float64) for name, values in {"equity": equity, **columns}.items()}
    return pl.DataFrame({"date": [day(d) for d in days], **floats})


def calendar(days: list[str]) -> pl.Series:
    return pl.Series("date", [day(d) for d in days])


def golden(spec: MetricSpec = SPEC, **inputs) -> dict[str, Metric]:
    """Metrics of Golden A with extra daily columns, positions or orders."""
    frames = {k: inputs.pop(k) for k in ("positions", "orders") if k in inputs}
    return compute_metrics(daily(GOLDEN_DAYS, GOLDEN_EQUITY, **inputs), calendar(GOLDEN_DAYS), spec, **frames)


def weekday_run(equity: list, spec: MetricSpec = SPEC) -> dict[str, Metric]:
    days = weekdays(len(equity))
    return compute_metrics(daily(days, equity), calendar(days), spec)


def values(metrics: dict[str, Metric]) -> dict:
    return {key: metric.value for key, metric in metrics.items()}


def reasons(metrics: dict[str, Metric]) -> dict:
    return {key: metric.reason for key, metric in metrics.items() if metric.reason}


def positions(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame([(day(d), code, shares) for d, code, shares in rows], schema=POSITIONS, orient="row")


def orders(statuses: list[str]) -> pl.DataFrame:
    return pl.DataFrame([(day("2024-02-01"), "600000.SH", s) for s in statuses], schema=ORDERS, orient="row")


# ------------------------------------------------------------ Golden A


def test_golden_a_returns():
    m = values(golden())
    assert m["trading_days"] == 4
    assert m["total_return"] == pytest.approx(0.188, abs=1e-12)
    assert m["annual_return"] == pytest.approx(0.188, abs=1e-12)
    assert m["annual_volatility"] == pytest.approx(2 * math.sqrt(0.05 / 3), abs=1e-12)
    assert m["annual_volatility"] == pytest.approx(0.2581988897, abs=1e-10)
    assert m["sharpe"] == pytest.approx(math.sqrt(0.6), abs=1e-12)
    assert m["sharpe"] == pytest.approx(0.7745966692, abs=1e-10)
    assert m["annual_return_ex_best"] == pytest.approx(-0.01, abs=1e-12)


def test_golden_a_drawdown():
    m = values(golden())
    assert m["max_drawdown"] == pytest.approx(0.1, abs=1e-12)
    assert m["max_drawdown_peak"] == day("2024-02-01")
    assert m["max_drawdown_trough"] == day("2024-02-02")
    assert m["max_drawdown_recovery"] == day("2024-03-01")
    assert m["max_drawdown_days"] == 2 and m["max_days_without_new_high"] == 2


def test_golden_a_win_rates():
    m = values(golden())
    assert (m["win_rate_daily"], m["win_rate_weekly"], m["win_rate_monthly"]) == pytest.approx((0.5, 1 / 3, 0.5))


def test_golden_a_with_a_risk_free_rate():
    rf = 1.05**4 - 1
    spec = MetricSpec(periods_per_year=4, risk_free_rate=rf, tail_fraction=0.25)
    metrics = golden(spec)
    assert metrics["sharpe"].value == pytest.approx(0.0, abs=1e-12)
    assert json.loads(metrics_json(metrics, spec))["spec"]["risk_free_rate"] == rf


def test_golden_a_with_a_benchmark():
    m = values(golden(benchmark=GOLDEN_BENCHMARK))
    assert m["tracking_error"] == pytest.approx(0.0938083152, abs=1e-10)
    assert m["information_ratio"] == pytest.approx(1.7056057308, abs=1e-10)
    rates = (m["excess_win_rate_daily"], m["excess_win_rate_weekly"], m["excess_win_rate_monthly"])
    assert rates == pytest.approx((0.75, 2 / 3, 1.0))
    assert m["benchmark_missing_days"] == 0


def test_golden_a_with_a_missing_benchmark_day():
    metrics = golden(benchmark=[100, 105, 92.4, None, 102.6564])
    m = values(metrics)
    assert m["benchmark_missing_days"] == 2
    assert m["tracking_error"] == pytest.approx(0.03 * math.sqrt(2), abs=1e-12)
    assert m["tracking_error"] == pytest.approx(0.0424264069, abs=1e-10)
    assert m["information_ratio"] == pytest.approx(3.2998316455, abs=1e-10)
    assert (m["excess_win_rate_daily"], m["excess_win_rate_weekly"]) == (1.0, 1.0)
    assert m["excess_win_rate_monthly"] is None and metrics["excess_win_rate_monthly"].reason == "no_periods"


def test_a_nan_benchmark_raises():
    with pytest.raises(ValueError, match="NaN"):
        golden(benchmark=[100, 105, 92.4, math.nan, 102.6564])


def test_no_benchmark_column():
    expected = {
        key: "no_benchmark"
        for key in KEYS
        if key.startswith("excess") or key in ("benchmark_missing_days", "tracking_error", "information_ratio")
    }
    assert {k: r for k, r in reasons(golden()).items() if r == "no_benchmark"} == expected


def test_an_all_null_benchmark():
    metrics = golden(benchmark=[None] * 5)
    assert metrics["benchmark_missing_days"].value == 4
    assert metrics["tracking_error"].reason == "too_few_benchmark_days"
    assert metrics["information_ratio"].reason == "too_few_benchmark_days"
    assert metrics["excess_win_rate_daily"].reason == "no_periods"


@pytest.mark.parametrize("scale", [7.3, 1000.0])
def test_holding_the_benchmark_at_any_scale_has_no_active_return(scale):
    days, benchmark = weekdays(8), [100, 103.7, 99.1, 101.3, 104.9, 102.2, 106.8, 105.5]
    equity = [scale * b for b in benchmark]  # each return then differs from the benchmark's only by rounding
    metrics = compute_metrics(daily(days, equity, benchmark=benchmark), calendar(days), SPEC)
    m = values(metrics)
    assert m["tracking_error"] == 0.0 and metrics["information_ratio"].reason == "zero_volatility"
    assert m["excess_win_rate_daily"] == m["excess_win_rate_weekly"] == m["excess_win_rate_monthly"] == 0.0


def test_constant_growth_has_no_volatility():
    metrics = weekday_run(list(100 * 1.01 ** np.arange(8)))
    assert metrics["annual_volatility"].value == 0.0 and metrics["sharpe"].reason == "zero_volatility"
    assert metrics["win_rate_daily"].value == 1.0


# ------------------------------------------------------------ trades, positions and orders


def test_turnover():
    metrics = golden(bought=[0, 100, 0, 49.5, 0], sold=[0, 0, 0, 49.5, 0])
    assert metrics["turnover"].value == pytest.approx(1.0, abs=1e-12)


def test_turnover_needs_both_trade_columns():
    with pytest.raises(ValueError, match="both"):
        golden(bought=[0, 100, 0, 49.5, 0])
    assert golden()["turnover"].reason == "no_trade_values"


def test_avg_holding_days_with_fractional_shares():
    held = positions(
        [
            ("2024-02-01", "X", 100.0),
            ("2024-02-02", "X", 100.0),
            ("2024-03-01", "X", 100.0),
            ("2024-02-01", "Y", 0.0),
            ("2024-02-02", "Y", 1000.0),
            ("2024-02-05", "Y", 1019.417),
            ("2024-03-01", "Y", 1019.417),
        ]
    )
    assert golden(positions=held)["avg_holding_days"].value == 2.0


def test_holding_episodes_are_split_by_code():
    held = positions(
        [("2024-02-01", "X", 1.0), ("2024-02-02", "X", 1.0), ("2024-02-05", "Y", 1.0), ("2024-03-01", "Y", 1.0)]
    )
    assert golden(positions=held)["avg_holding_days"].value == 2.0  # Y starting the day after X ends is a new episode


def test_avg_holding_days_without_holdings():
    assert golden()["avg_holding_days"].reason == "no_positions"
    assert golden(positions=positions([]))["avg_holding_days"].reason == "never_held"
    assert golden(positions=positions([("2024-02-01", "X", 0.0)]))["avg_holding_days"].reason == "never_held"


@pytest.mark.parametrize(
    ("held", "message"),
    [
        pytest.param(positions([("2024-01-31", "X", 1.0)]), "not a run day", id="base_day"),
        pytest.param(positions([("2024-02-03", "X", 1.0)]), "not a run day", id="not_a_trading_day"),
        pytest.param(positions([("2024-02-01", "X", 1.0), ("2024-02-01", "X", 2.0)]), "repeat", id="repeated"),
        pytest.param(positions([("2024-02-01", "X", math.nan)]), "finite shares", id="nan_shares"),
        pytest.param(positions([("2024-02-01", "X", None)]), "finite shares", id="null_shares"),
        pytest.param(positions([("2024-02-01", None, 1.0)]), "a code", id="null_code"),
        pytest.param(
            positions([("2024-02-01", "X", 1.0)]).with_columns(pl.col("shares").cast(pl.Int64)),
            "not Float64",
            id="int_shares",
        ),
    ],
)
def test_bad_positions_raise(held, message):
    with pytest.raises(ValueError, match=message):
        golden(positions=held)


def test_executable_order_share():
    statuses = ["filled", "filled", "partial", "rejected", "rejected"]
    assert golden(orders=orders(statuses))["executable_order_share"].value == pytest.approx(0.6)
    assert golden()["executable_order_share"].reason == "no_orders"
    assert golden(orders=orders([]))["executable_order_share"].reason == "no_rebalance_orders"
    with pytest.raises(ValueError, match="cancelled"):
        golden(orders=orders(["filled", "cancelled"]))
    with pytest.raises(ValueError, match="unknown"):
        golden(orders=orders([None]))


# ------------------------------------------------------------ degenerate runs


def test_a_one_day_run():
    metrics = weekday_run([100, 90])
    m = values(metrics)
    assert m["total_return"] == pytest.approx(-0.1) and m["annual_return"] == pytest.approx(-0.3439)
    assert metrics["annual_volatility"].reason == metrics["sharpe"].reason == "too_few_periods"
    assert m["max_drawdown"] == pytest.approx(0.1) and m["max_drawdown_days"] == 1
    assert (m["max_drawdown_peak"], m["max_drawdown_trough"]) == (day("2024-01-02"), day("2024-01-03"))
    assert metrics["max_drawdown_recovery"].reason == "not_recovered"
    assert metrics["annual_return_ex_best"].reason == "too_few_periods"


def test_a_recovered_drawdown():
    m = values(weekday_run([100, 90, 95, 100, 101]))
    assert m["max_drawdown_recovery"] == day("2024-01-05") and m["max_drawdown_days"] == 2


def test_a_flat_run():
    metrics = weekday_run([100] * 5)
    m = values(metrics)
    assert m["annual_volatility"] == 0.0 and metrics["sharpe"].reason == "zero_volatility"
    assert m["max_drawdown"] == 0.0 and m["max_drawdown_days"] == 0 and m["max_days_without_new_high"] == 0
    for key in ("max_drawdown_peak", "max_drawdown_trough", "max_drawdown_recovery"):
        assert metrics[key].reason == "no_drawdown"
    assert m["win_rate_daily"] == m["win_rate_weekly"] == m["win_rate_monthly"] == 0.0
    assert m["annual_return_ex_best"] == 0.0


def test_the_deepest_drawdown_need_not_be_the_longest():
    m = values(weekday_run([100, 95, 100, 99, 98.5, 99.5, 99.8, 101]))
    assert m["max_drawdown"] == pytest.approx(0.05) and m["max_drawdown_days"] == 1
    assert m["max_drawdown_recovery"] == day("2024-01-04") and m["max_days_without_new_high"] == 4


def test_ex_best_drops_a_day_from_twenty_run_days():
    spec = MetricSpec(periods_per_year=4, risk_free_rate=0.0, tail_fraction=0.05)
    equity = list(100 * 1.01 ** np.arange(20))
    assert weekday_run(equity, spec)["annual_return_ex_best"].reason == "too_few_periods"
    expected = (1.01**19) ** (4 / 20) - 1  # 19 gains of 1% and a best day of 5%, which is dropped
    assert weekday_run([*equity, equity[-1] * 1.05], spec)["annual_return_ex_best"].value == pytest.approx(expected)


def test_ex_best_rounds_before_counting_the_dropped_days():
    spec = MetricSpec(periods_per_year=4, risk_free_rate=0.0, tail_fraction=0.58)  # 0.58 * 50 = 28.999999999999996
    equity = list(100 * np.cumprod(np.r_[1.0, 1 + np.arange(50, 0, -1) / 1000]))  # gains of 5.0%, 4.9%, ..., 0.1%
    expected = math.prod(1 + j / 1000 for j in range(1, 22)) ** (4 / 50) - 1  # the 29 best days dropped
    assert weekday_run(equity, spec)["annual_return_ex_best"].value == pytest.approx(expected, rel=1e-12)


# ------------------------------------------------------------ contract violations


def bad_daily() -> list:
    days, equity = weekdays(4), [100, 101, 102, 103]
    good = daily(days, equity)
    return [
        pytest.param(good.drop("equity"), "no column", id="no_equity"),
        pytest.param(good.with_columns(pl.col("equity").cast(pl.Int64)), "not Float64", id="int_equity"),
        pytest.param(good.reverse(), "strictly increasing", id="unsorted"),
        pytest.param(daily([days[0], days[1], days[1], days[3]], equity), "strictly increasing", id="duplicate"),
        pytest.param(good.filter(pl.col("date") != day(days[2])), "exactly the calendar", id="missing_day"),
        pytest.param(
            good.with_columns(pl.col("date").replace({day(days[3]): day("2024-01-06")})),
            "exactly the calendar",
            id="extra_day",
        ),
        pytest.param(good.head(1), "base day", id="one_row"),
        pytest.param(daily(days, [100, 0, 102, 103]), "equity must be finite", id="zero"),
        pytest.param(daily(days, [100, -1, 102, 103]), "equity must be finite", id="negative"),
        pytest.param(daily(days, [100, math.nan, 102, 103]), "equity must be finite", id="nan"),
        pytest.param(daily(days, [100, None, 102, 103]), "equity must be finite", id="null"),
        pytest.param(daily(days, equity, bought=[0, -1, 0, 0], sold=[0] * 4), "non-negative", id="negative_trade"),
        pytest.param(daily(days, equity, benchmark=[100, 0, 101, 102]), "benchmark must be", id="zero_benchmark"),
        pytest.param(daily(days, equity, benchmark=[100, math.inf, 101, 102]), "benchmark must be", id="inf_benchmark"),
    ]


@pytest.mark.parametrize(("frame", "message"), bad_daily())
def test_bad_daily_raises(frame, message):
    with pytest.raises(ValueError, match=message):
        compute_metrics(frame, calendar(weekdays(4)), SPEC)


def test_a_calendar_of_datetimes_raises():
    days = weekdays(4)
    with pytest.raises(ValueError, match="not Date"):
        compute_metrics(daily(days, [100, 101, 102, 103]), calendar(days).cast(pl.Datetime), SPEC)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("periods_per_year", 0),
        ("periods_per_year", True),
        ("periods_per_year", 242.0),
        ("tail_fraction", 1.0),
        ("tail_fraction", -0.1),
        ("risk_free_rate", math.nan),
        ("risk_free_rate", -1.0),
    ],
)
def test_bad_spec_raises(field, value):
    with pytest.raises(ValueError, match=field):
        MetricSpec(**{field: value})


def test_a_metric_has_a_reason_exactly_when_it_has_no_value():
    for bad in [(None, "ratio", None), (1.0, "ratio", "zero_volatility"), (None, "ratio", "why"), (math.inf, "ratio")]:
        with pytest.raises(ValueError):
            Metric(*bad)


# ------------------------------------------------------------ random runs against a plain-Python reference


def random_run(seed: int) -> dict:
    """Inputs of a 300-day run inside a longer calendar, with benchmark gaps, trades, fractional positions, orders."""
    rng = np.random.default_rng(seed)
    span = np.arange(np.datetime64("2019-12-02"), np.datetime64("2021-07-01"))
    open_days = span[np.is_busday(span) & (rng.random(span.size) > 0.03)]
    first = int(rng.integers(1, open_days.size - 302))
    days = open_days[first : first + 301]
    equity = 1e6 * np.cumprod(np.r_[1.0, 1 + rng.normal(4e-4, 0.015, 300)])
    benchmark = np.where(rng.random(301) < 0.03, np.nan, 4000 * np.cumprod(1 + rng.normal(3e-4, 0.012, 301)))
    trades = rng.random((2, 301)) * (rng.random((2, 301)) < 0.6) * 0.2 * equity
    held = np.cumsum(rng.random((5, 300)) < 0.15, axis=1) % 2 == 1
    shares = np.where(held, rng.uniform(100, 5000, held.shape), 0.0)
    rows = [
        (days[t + 1].item(), f"C{c}", shares[c, t]) for c, t in zip(*np.nonzero(held | (rng.random(held.shape) < 0.05)))
    ]
    statuses = list(rng.choice(ORDER_STATUSES, int(rng.integers(0, 25))))
    return {
        "calendar": pl.Series("date", open_days),
        "daily": pl.DataFrame(
            {
                "date": days,
                "equity": equity,
                "benchmark": pl.Series(benchmark).fill_nan(None),
                "bought": trades[0],
                "sold": trades[1],
            }
        ),
        "positions": pl.DataFrame(rows, schema=POSITIONS, orient="row"),
        "orders": pl.DataFrame([(days[1].item(), "C0", s) for s in statuses], schema=ORDERS, orient="row"),
    }


def run_metrics(run: dict, spec: MetricSpec) -> dict[str, Metric]:
    return compute_metrics(run["daily"], run["calendar"], spec, run["positions"], run["orders"])


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def stdev(xs: list[float]) -> float:
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def compounded(returns: list, keys: list) -> list:
    """Compound return of each run of equal keys; None if a day in it is None."""
    growth, last = [], object()
    for r, key in zip(returns, keys):
        if key != last:
            growth.append(1.0)
            last = key
        growth[-1] = None if r is None or growth[-1] is None else growth[-1] * (1 + r)
    return [None if g is None else g - 1 for g in growth]


def reference(run: dict, spec: MetricSpec) -> dict:
    """Every metric by plain Python loops over its definition."""
    frame = run["daily"]
    days, equity, bench = frame["date"].to_list(), frame["equity"].to_list(), frame["benchmark"].to_list()
    k, n = spec.periods_per_year, len(equity) - 1
    nav = [v / equity[0] for v in equity]
    r = [equity[t] / equity[t - 1] - 1 for t in range(1, n + 1)]
    rb = [None if None in (bench[t], bench[t - 1]) else bench[t] / bench[t - 1] - 1 for t in range(1, n + 1)]
    rf = (1 + spec.risk_free_rate) ** (1 / k) - 1
    growth = 1.0
    for x in sorted(r)[: n - math.floor(round(spec.tail_fraction * n, 9))]:
        growth *= 1 + x
    out = {
        "trading_days": n,
        "total_return": nav[n] - 1,
        "annual_return": nav[n] ** (k / n) - 1,
        "annual_volatility": stdev(r) * math.sqrt(k),
        "sharpe": mean([x - rf for x in r]) / stdev(r) * math.sqrt(k),
        "annual_return_ex_best": growth ** (k / n) - 1,
        **reference_drawdown(days, nav),
    }
    weeks, months = [d.isocalendar()[:2] for d in days[1:]], [(d.year, d.month) for d in days[1:]]
    for name, own, other in (
        ("daily", r, rb),
        ("weekly", compounded(r, weeks), compounded(rb, weeks)),
        ("monthly", compounded(r, months), compounded(rb, months)),
    ):
        pairs = [(x, y) for x, y in zip(own, other) if y is not None]
        out[f"win_rate_{name}"] = sum(x > 0 for x in own) / len(own)
        out[f"excess_win_rate_{name}"] = sum(x > y for x, y in pairs) / len(pairs) if pairs else None
    active = [x - y for x, y in zip(r, rb) if y is not None]
    bought, sold = frame["bought"].to_list(), frame["sold"].to_list()
    statuses = run["orders"]["status"].to_list()
    return out | {
        "benchmark_missing_days": rb.count(None),
        "tracking_error": stdev(active) * math.sqrt(k),
        "information_ratio": mean(active) / stdev(active) * math.sqrt(k),
        "turnover": k / n * sum((bought[t] + sold[t]) / (2 * equity[t - 1]) for t in range(1, n + 1)),
        "avg_holding_days": reference_holding(days, run["positions"].rows()),
        "executable_order_share": sum(s in ("filled", "partial") for s in statuses) / len(statuses)
        if statuses
        else None,
    }


def reference_drawdown(days: list, nav: list) -> dict:
    high, dd = nav[0], []
    for v in nav:
        high = max(high, v)
        dd.append(1 - v / high)
    trough = dd.index(max(dd))
    peak = max(t for t in range(trough) if dd[t] == 0)
    recovery = next((t for t in range(trough, len(nav)) if dd[t] == 0), None)
    longest = below = 0
    for d in dd:
        below = below + 1 if d > 0 else 0
        longest = max(longest, below)
    return {
        "max_drawdown": dd[trough],
        "max_drawdown_peak": days[peak],
        "max_drawdown_trough": days[trough],
        "max_drawdown_recovery": None if recovery is None else days[recovery],
        "max_drawdown_days": (len(nav) if recovery is None else recovery) - peak - 1,
        "max_days_without_new_high": longest,
    }


def reference_holding(days: list, rows: list) -> float | None:
    number = {d: i for i, d in enumerate(days)}
    held: dict[str, list[int]] = {}
    for d, code, shares in rows:
        if shares > 0:
            held.setdefault(code, []).append(number[d])
    count = episodes = 0
    for i in held.values():
        i.sort()
        count += len(i)
        episodes += 1 + sum(b - a != 1 for a, b in zip(i, i[1:]))
    return count / episodes if count else None


def assert_same(metrics: dict[str, Metric], expected: dict) -> None:
    assert set(metrics) == set(expected)
    for key, metric in metrics.items():
        if isinstance(expected[key], float):
            assert metric.value == pytest.approx(expected[key], rel=1e-12, abs=0), key
        else:
            assert metric.value == expected[key], key


@pytest.mark.parametrize("seed", range(50))
def test_random_runs_match_the_reference(seed):
    spec = MetricSpec(periods_per_year=242, risk_free_rate=0.02, tail_fraction=0.05)
    run = random_run(seed)
    assert_same(run_metrics(run, spec), reference(run, spec))


def test_scale_changes_no_metric():
    spec = MetricSpec()
    run = random_run(7)
    base = values(run_metrics(run, spec))
    money = run | {"daily": run["daily"].with_columns(pl.col("equity", "bought", "sold") * 7.3)}
    index = run | {"daily": run["daily"].with_columns(pl.col("benchmark") * 7.3)}
    shares = run | {"positions": run["positions"].with_columns(pl.col("shares") * 7.3)}
    for scaled in (money, index):
        assert_same(run_metrics(scaled, spec), base)
    assert values(run_metrics(shares, spec)) == base


# ------------------------------------------------------------ canonical bytes


def test_compute_metrics_keys():
    assert list(golden()) == KEYS
    assert list(run_metrics(random_run(0), MetricSpec())) == KEYS


def test_metrics_json_layout():
    document = json.loads(metrics_json(golden(), SPEC))
    assert list(document) == ["version", "spec", "metrics"] and document["version"] == METRICS_VERSION
    assert document["spec"] == {"periods_per_year": 4, "risk_free_rate": 0.0, "tail_fraction": 0.25}
    assert list(document["metrics"]) == KEYS
    assert document["metrics"]["max_drawdown_peak"] == {"value": "2024-02-01", "unit": "date"}
    assert document["metrics"]["sharpe"] == {"value": golden()["sharpe"].value, "unit": "ratio"}
    assert document["metrics"]["tracking_error"] == {"value": None, "unit": "fraction", "reason": "no_benchmark"}


def test_every_null_has_a_reason_and_no_value_has_one():
    runs = [golden(), golden(benchmark=[None] * 5), weekday_run([100] * 5), weekday_run([100, 90])]
    for metrics in runs:
        for entry in json.loads(metrics_json(metrics, SPEC))["metrics"].values():
            assert ("reason" in entry) == (entry["value"] is None)


def test_metrics_json_is_the_same_after_a_parquet_round_trip(tmp_path):
    spec = MetricSpec()
    run = random_run(3)
    frames = run | {"calendar": run["calendar"].to_frame()}
    for name, frame in frames.items():
        frame.write_parquet(tmp_path / f"{name}.parquet")
    loaded = {name: pl.read_parquet(tmp_path / f"{name}.parquet") for name in frames}
    loaded["calendar"] = loaded["calendar"]["date"]
    expected = metrics_json(run_metrics(run, spec), spec)
    assert metrics_json(run_metrics(run, spec), spec) == expected
    assert metrics_json(run_metrics(loaded, spec), spec) == expected
    assert expected.endswith(b"}\n")
