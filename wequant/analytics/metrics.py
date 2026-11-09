"""Every run metric, defined once, and the canonical bytes of a run's metrics."""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from wequant.analytics.inputs import EXECUTED, RunSeries, check_orders, held_days, known_fills, run_series
from wequant.analytics.series import annualize, compound, drawdowns, period_keys, runs

METRICS_VERSION = 2  # bump when a metric is added or its definition changes
UNITS = ("fraction", "ratio", "days", "date", "count")
REASONS = (
    "too_few_periods",
    "zero_volatility",
    "no_benchmark",
    "too_few_benchmark_days",
    "no_periods",
    "no_drawdown",
    "not_recovered",
    "no_trade_values",
    "no_positions",
    "never_held",
    "no_orders",
    "no_rebalance_orders",
    "no_order_amounts",
    "no_fills",
    "no_day_amounts",
)
_CAPACITY = {"capacity_max": "fraction", "capacity_p95": "fraction", "capacity_unknown_fills": "count"}
_PERIODS = {"daily": None, "weekly": "week", "monthly": "month"}  # win-rate name -> period_keys period
_ROUNDING = 1e-12  # return gaps and stds up to this are float rounding: V_t / V_{t-1} - 1 is off by ~1e-16


@dataclass(frozen=True)
class MetricSpec:
    """The conventions a run's metrics are computed with."""

    periods_per_year: int = 242
    risk_free_rate: float = 0.0
    tail_fraction: float = 0.05  # share of best days annual_return_ex_best drops

    def __post_init__(self) -> None:
        if type(self.periods_per_year) is not int or self.periods_per_year <= 0:  # bool is an int subclass
            raise ValueError(f"periods_per_year must be a positive int, not {self.periods_per_year!r}")
        if not 0 <= self.tail_fraction < 1:
            raise ValueError(f"tail_fraction must be in [0, 1), not {self.tail_fraction!r}")
        if not (math.isfinite(self.risk_free_rate) and self.risk_free_rate > -1):
            raise ValueError(f"risk_free_rate must be finite and above -1, not {self.risk_free_rate!r}")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "periods_per_year": self.periods_per_year,
            "risk_free_rate": float(self.risk_free_rate) + 0.0,  # -0.0 becomes 0.0, as in every archived form
            "tail_fraction": float(self.tail_fraction) + 0.0,
        }


@dataclass(frozen=True)
class Metric:
    """One metric value; None with a REASONS code when it is not available."""

    value: float | int | dt.date | None
    unit: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.unit not in UNITS or self.reason not in (None, *REASONS):
            raise ValueError(f"unknown unit {self.unit!r} or reason {self.reason!r}")
        if (self.value is None) != (self.reason is not None):
            raise ValueError(f"a reason is set exactly when the value is None: {self.value!r}, {self.reason!r}")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError(f"metric value {self.value} is not finite")

    def to_dict(self) -> dict:
        value = self.value.isoformat() if isinstance(self.value, dt.date) else self.value
        return {"value": value, "unit": self.unit} | ({"reason": self.reason} if self.value is None else {})


Metrics = dict[str, Metric]


def compute_metrics(
    daily: pl.DataFrame,
    calendar: pl.Series,
    spec: MetricSpec = MetricSpec(),
    positions: pl.DataFrame | None = None,
    orders: pl.DataFrame | None = None,
) -> Metrics:
    """Every metric of one run, in definition order."""
    s = run_series(daily, calendar)
    return {
        **return_metrics(s, spec),
        **drawdown_metrics(s),
        **win_rates(s),
        **benchmark_metrics(s, spec),
        "turnover": turnover(s, spec),
        "avg_holding_days": avg_holding_days(positions, s),
        "executable_order_share": executable_order_share(orders),
        **capacity(orders),
    }


def return_metrics(s: RunSeries, spec: MetricSpec) -> Metrics:
    """Total and annual return, annual volatility, Sharpe and the annual return without the best days."""
    k = spec.periods_per_year
    rf_daily = (1 + spec.risk_free_rate) ** (1 / k) - 1
    return {
        "trading_days": Metric(s.n, "days"),
        "total_return": Metric(float(s.nav[-1] - 1), "fraction"),
        "annual_return": Metric(annualize(s.nav[-1], s.n, k), "fraction"),
        "annual_volatility": _annual_std(s.returns, k, "too_few_periods"),
        "sharpe": _sharpe_like(s.returns, rf_daily, k, "too_few_periods"),
        "annual_return_ex_best": _annual_return_ex_best(s, spec),
    }


def drawdown_metrics(s: RunSeries) -> Metrics:
    """The deepest drawdown from the initial capital on, its dates and length, and the longest stretch below a high."""
    dd = drawdowns(s.nav)
    starts, stops = runs(dd > 0)
    if starts.size:
        trough = int(np.argmax(dd))
        episode = np.searchsorted(starts, trough, side="right") - 1
        start, stop = int(starts[episode]), int(stops[episode])
        peak, low, back = _day(s, start - 1), _day(s, trough), _day(s, stop)
    else:
        start = stop = 0
        peak = low = back = Metric(None, "date", "no_drawdown")
    return {
        "max_drawdown": Metric(float(dd.max()), "fraction"),
        "max_drawdown_peak": peak,
        "max_drawdown_trough": low,
        "max_drawdown_recovery": back,
        "max_drawdown_days": Metric(stop - start, "days"),
        "max_days_without_new_high": Metric(int((stops - starts).max(initial=0)), "days"),
    }


def win_rates(s: RunSeries) -> Metrics:
    """Share of days, weeks and months with a gain, then with a gain over the benchmark."""
    days = s.dates[1:]
    own = {name: _per_period(s.returns, days, period) for name, period in _PERIODS.items()}
    rates = {f"win_rate_{name}": _win_rate(r, 0.0) for name, r in own.items()}
    if s.benchmark_returns is None:
        return rates | {f"excess_win_rate_{name}": Metric(None, "fraction", "no_benchmark") for name in own}
    return rates | {
        f"excess_win_rate_{name}": _win_rate(own[name], _per_period(s.benchmark_returns, days, period))
        for name, period in _PERIODS.items()
    }


def benchmark_metrics(s: RunSeries, spec: MetricSpec) -> Metrics:
    """Run days without a benchmark return, tracking error and information ratio."""
    if s.benchmark_returns is None:
        return {
            "benchmark_missing_days": Metric(None, "days", "no_benchmark"),
            "tracking_error": Metric(None, "fraction", "no_benchmark"),
            "information_ratio": Metric(None, "ratio", "no_benchmark"),
        }
    paired = np.isfinite(s.benchmark_returns)
    active = s.returns[paired] - s.benchmark_returns[paired]
    k = spec.periods_per_year
    return {
        "benchmark_missing_days": Metric(int((~paired).sum()), "days"),
        "tracking_error": _annual_std(active, k, "too_few_benchmark_days"),
        "information_ratio": _sharpe_like(active, 0.0, k, "too_few_benchmark_days"),
    }


def turnover(s: RunSeries, spec: MetricSpec) -> Metric:
    """Annualized one-way turnover: the yearly average of (bought + sold) / 2 over the previous day's equity."""
    if s.turnover is None:
        return Metric(None, "fraction", "no_trade_values")
    return Metric(float(spec.periods_per_year / s.n * s.turnover.sum()), "fraction")


def avg_holding_days(positions: pl.DataFrame | None, s: RunSeries) -> Metric:
    """Held days per holding episode, an episode being a code's run of consecutive held days."""
    if positions is None:
        return Metric(None, "days", "no_positions")
    held = held_days(positions, s.dates)
    if held.is_empty():
        return Metric(None, "days", "never_held")
    episodes = held.select((pl.col("i").diff().over("code") != 1).fill_null(True).sum()).item()
    return Metric(held.height / episodes, "days")


def executable_order_share(orders: pl.DataFrame | None) -> Metric:
    """Share of rebalance orders that were filled in full or in part."""
    if orders is None:
        return Metric(None, "fraction", "no_orders")
    status = check_orders(orders)
    if status.is_empty():
        return Metric(None, "fraction", "no_rebalance_orders")
    return Metric(float(status.is_in(EXECUTED).mean()), "fraction")


def capacity(orders: pl.DataFrame | None) -> Metrics:
    """Each fill's value over its stock's traded amount that day: max, 95th percentile, fills of unknown amount."""
    got = None if orders is None else known_fills(orders)
    if got is None:
        reason = "no_orders" if orders is None else "no_order_amounts"
        return {name: Metric(None, unit, reason) for name, unit in _CAPACITY.items()}
    fills, unknown = got
    ratios = fills["day_share"].to_numpy()
    if ratios.size:
        top = Metric(float(ratios.max()), "fraction")
        p95 = Metric(float(np.percentile(ratios, 95)), "fraction")  # linear between order statistics
    else:
        top = p95 = Metric(None, "fraction", "no_day_amounts" if unknown else "no_fills")
    return {"capacity_max": top, "capacity_p95": p95, "capacity_unknown_fills": Metric(unknown, "count")}


def metrics_json(metrics: Metrics, spec: MetricSpec) -> bytes:
    """The canonical bytes of a run's metrics: keys in definition order, dates as ISO strings."""
    document = {
        "version": METRICS_VERSION,
        "spec": spec.to_dict(),
        "metrics": {key: metric.to_dict() for key, metric in metrics.items()},
    }
    return json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False).encode() + b"\n"


def _annual_std(x: np.ndarray, k: int, short: str) -> Metric:
    if x.size < 2:
        return Metric(None, "fraction", short)
    return Metric(_std(x) * math.sqrt(k), "fraction")


def _sharpe_like(x: np.ndarray, hurdle: float, k: int, short: str) -> Metric:
    if x.size < 2:
        return Metric(None, "ratio", short)
    std = _std(x)
    if not std:
        return Metric(None, "ratio", "zero_volatility")
    return Metric(float(np.mean(x - hurdle) / std * math.sqrt(k)), "ratio")


def _std(x: np.ndarray) -> float:
    """Sample standard deviation, 0.0 when it is only float rounding."""
    std = float(np.std(x, ddof=1))
    return std if std > _ROUNDING else 0.0


def _annual_return_ex_best(s: RunSeries, spec: MetricSpec) -> Metric:
    dropped = math.floor(round(spec.tail_fraction * s.n, 9))
    if not dropped:
        return Metric(None, "fraction", "too_few_periods")
    kept = np.sort(s.returns)[: s.n - dropped]
    return Metric(annualize(np.prod(1 + kept), s.n, spec.periods_per_year), "fraction")


def _day(s: RunSeries, i: int) -> Metric:
    if i > s.n:
        return Metric(None, "date", "not_recovered")
    return Metric(s.dates[i].item(), "date")


def _per_period(returns: np.ndarray, days: np.ndarray, period: str | None) -> np.ndarray:
    return returns if period is None else compound(returns, period_keys(days, period))


def _win_rate(returns: np.ndarray, hurdle: np.ndarray | float) -> Metric:
    valid = np.isfinite(returns) & np.isfinite(hurdle)
    if not valid.any():
        return Metric(None, "fraction", "no_periods")
    return Metric(float(np.mean((returns - hurdle > _ROUNDING)[valid])), "fraction")
