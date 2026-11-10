"""Warning texts: one per code in the run's order, every param read, every number and date of a text a param."""

import re

import pytest

from wequant.backtest import WARNINGS
from wequant.backtest.messages import TEXTS, warning

EXAMPLES = {  # each code's params with the keys its warning carries
    "limits_unchecked": {},
    "st_unknown": {"known_share": 0.375},
    "st_band_fills": {"buys": 3, "sells": 4},
    "stale_holdings": {
        "codes": 7,
        "stale_days": 20,
        "share": 0.0125,
        "day": "2026-11-06",
        "worst_share": 0.1825,
        "worst_day": "2018-06-25",
    },
    "no_picks": {},
    "index_uncovered": {"index": "000852.SH", "days": 9, "first": None, "index_2": "000905.SH", "days_2": 3},
    "list_days_approximate": {"codes_at_calendar_start": 1234, "cells": 56},
    "max_weight_cash": {"days": 12, "mean_share": 0.0625},
}
EXAMPLES["index_uncovered"]["first_2"] = "2007-01-15"


def test_every_code_has_a_text_in_the_runs_order():
    assert tuple(TEXTS) == WARNINGS and EXAMPLES.keys() == TEXTS.keys()


@pytest.mark.parametrize("code", list(EXAMPLES))
def test_a_text_reads_every_param_and_shows_no_other_number(code):
    params = EXAMPLES[code]
    text, kept = warning(code, params)
    assert kept is params
    for key in params:
        with pytest.raises(KeyError):
            TEXTS[code]({k: v for k, v in params.items() if k != key})
    for shown in sorted((r for value in params.values() for r in _renderings(value)), key=len, reverse=True):
        text = text.replace(shown, "")
    assert not re.search(r"[0-9]", text), text


def _renderings(value) -> list[str]:
    """The ways a text may show a param."""
    if isinstance(value, float):
        return [f"{value:.1%}", f"{value:,.2f}", str(value)]
    return [f"{value:,}", str(value)] if isinstance(value, int) else [str(value)]
