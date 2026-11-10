"""The English text of each run warning, filled from its params: every number and date of a text is one of them."""

from __future__ import annotations

import math
from collections.abc import Callable

Params = dict[str, int | float | str | None]  # dates as ISO text, shares as fractions; None: does not apply


def _index_uncovered(p: Params) -> str:
    """One clause per index: index, days and first, then index_2, days_2 and first_2, and so on."""
    suffixes = ["", *(f"_{k}" for k in range(2, math.ceil(len(p) / 3) + 1))]
    return "; ".join(_uncovered(*(p[f"{name}{suffix}"] for name in ("index", "days", "first"))) for suffix in suffixes)


def _uncovered(index: str, days: int, first: str | None) -> str:
    span = f" from {first}" if first is not None else ", which is empty"
    return f"member@{index}: {days} decision days outside its index_members span{span}"


def _data_anomaly(p: Params) -> str:
    """Cells by level, decisions, fills, the largest holding on them and, when levels are excluded, what stays used."""
    used = (
        f"{p['codes']} codes, {p['cells']} anomalous cells used ({p['error_cells']} error, {p['suspicious_cells']} "
        f"suspicious, {p['notice_cells']} notice): {p['decisions']} decisions read them in signal windows, {p['fills']} "
        "orders on them, "
    )
    held = (
        "no holding valued on them"
        if p["max_share_day"] is None
        else (f"holdings on them peaked at {p['max_share']:.1%} of equity on {p['max_share_day']}")
    )
    kept = "" if p["unavoidable"] is None else f"; {p['unavoidable']} uses of excluded levels no missing day removes"
    return used + held + kept


TEXTS: dict[str, Callable[[Params], str]] = {
    "limits_unchecked": lambda p: (
        "the store's price limits are not checked against its bars; run tools.check_limits --commit"
    ),
    "st_unknown": lambda p: (
        f"ST state known on {p['known_share']:.1%} of the universe cells on decision days; unknown reads as not ST"
    ),
    "st_band_fills": lambda p: (
        f"{p['buys']} buys at the +ST-band price and {p['sells']} sells at the -ST-band price where ST is unknown"
    ),
    "stale_holdings": lambda p: (
        f"{p['codes']} codes were held more than {p['stale_days']} trading days after their last bar, valued at their "
        f"last close: at most {p['worst_share']:.1%} of equity on {p['worst_day']}, {p['share']:.1%} on the last day "
        f"{p['day']}"
    ),
    "no_picks": lambda p: "no decision day selected a stock",
    "index_uncovered": _index_uncovered,
    "list_days_approximate": lambda p: (
        "list_days counts trading days from securities.first_date, the first day of the source's data, not the "
        f"listing date: a lower bound for the {p['codes_at_calendar_start']} codes whose data starts with the "
        f"calendar, which min_list_days left out of {p['cells']} decision-day cells"
    ),
    "max_weight_cash": lambda p: (
        f"{p['days']} decision days kept {p['mean_share']:.1%} of equity in cash on average: max_weight capped every "
        "target"
    ),
    "delisted_holdings": lambda p: (
        f"{p['codes']} delisted holdings were cleared at the open of their delisting day, worth {p['amount']:,.2f} "
        f"yuan at their last close, the largest {p['max_share']:.1%} of the previous close's equity; {p['mergers']} "
        "of them were classified as mergers or privatisations"
    ),
    "delisting_approximate": lambda p: (
        f"delisting days come from securities.last_date, not official dates: {p['earlier_codes']} holdings were "
        "written down to zero on the first trading day after it, possibly before their official delisting or while "
        f"only suspended; {p['maybe_suspended']} of them were not evidently forced delistings"
    ),
    "data_anomaly": _data_anomaly,
}


def warning(code: str, params: Params) -> tuple[str, Params]:
    """(text, params) of a warning: its text filled from params."""
    return TEXTS[code](params), params
