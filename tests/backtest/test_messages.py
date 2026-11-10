"""Design 6.1 (R-10): a warning's text is filled from its params, and every number and date in it is a param."""

import re

import pytest

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
    "delisted_holdings": {"codes": 40, "amount": 1857948.25, "max_share": 0.036, "mergers": 1},
    "delisting_approximate": {"earlier_codes": 25, "maybe_suspended": 3},
    "data_anomaly": {
        "codes": 2,
        "cells": 5,
        "error_cells": 1,
        "suspicious_cells": 3,
        "notice_cells": 1,
        "decisions": 6,
        "fills": 7,
        "max_share": 0.4375,
        "max_share_day": "2024-01-16",
        "unavoidable": 8,
    },
}
EXAMPLES["index_uncovered"]["first_2"] = "2007-01-15"


@pytest.mark.parametrize("code", list(TEXTS))  # one template per code
def test_a_text_reads_every_param_and_shows_no_other_number(code):
    params = EXAMPLES[code]
    text, _ = warning(code, params)
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
