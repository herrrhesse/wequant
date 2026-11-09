"""Hypothesis profiles and the synthetic panels of the expression tests; HYPOTHESIS_PROFILE=deep runs 2000 examples."""

import os
from functools import partial

import pytest
from hypothesis import settings

from tests.frames import table_frame
from wequant.data.store import Store
from wequant.expr import OPERATORS, Field
from wequant.panel import load_panel

settings.register_profile("default", deadline=None, max_examples=100, derandomize=True, database=None)
settings.register_profile("deep", settings.get_profile("default"), max_examples=2000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
SH, SZ, SZ2 = "600000.SH", "000001.SZ", "000002.SZ"
CLOSES = {  # 厘; None: no bar
    SH: [10000, 10500, 11550, None, 11000, 12100],
    SZ: [20000, 19000, 19500, 20500, 20000, 21000],
    SZ2: [None, 10000, 14000, 12000, 15000, 13000],
}
FLAT = {(SH, 2), (SZ, 5)}  # high = low = close
SZ_EX_DATE = 3  # factor 1.0 -> 1.1; the preclose stays the unadjusted previous close, so the limits stay simple
BY_ROLE = {"x": Field("close"), "s": Field("adj_close"), "c": Field("limit_up"), "w": 1}


def arguments(name: str) -> list:
    """Valid arguments of an operator by role: close, adj_close or limit_up, K + 1 bars, weight 1."""
    return [BY_ROLE[role] if role in BY_ROLE else int(role[1]) + 1 for role in OPERATORS[name].roles]


def bar(code: str, t: int, close: int, preclose: int | None, scale: int = 1) -> dict:
    """A bars row: open = close, high and low 0.10 yuan away except on flat days and on 600000.SH's limit-up D5."""
    high, low = (close, close) if (code, t) in FLAT else (close + 100, close - 100)
    if (code, t) == (SH, 5):
        high, low = close, 12000
    prices = {"open": close, "high": high, "low": low, "close": close}
    return {
        "code": code,
        "date": DAYS[t],
        **{k: v * scale for k, v in prices.items()},
        "volume": 1000 * (t + 1),
        "preclose": preclose,
    }


def bars(code: str, doubled: tuple[int, ...] = ()) -> list[dict]:
    """Every bar of code; prices on the days in doubled are twice as high, preclose unchanged."""
    rows, preclose = [], None
    for t, close in enumerate(CLOSES[code]):
        if close is not None:
            rows.append(bar(code, t, close, preclose, 2 if t in doubled else 1))
            preclose = close
    return rows


def security(code: str, first: int = 0) -> dict:
    return {"code": code, "exchange": code[-2:], "board": "main", "first_date": DAYS[first], "last_date": DAYS[-1]}


def seed(home, codes: dict[str, int], factors: list[dict], statuses: list[dict]) -> Store:
    """Version 1 of a store holding the bars of codes (code -> first day), with factors and daily_status rows."""
    store = Store(home)
    store.commit(
        {"source": "test"},
        replace={
            "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
            "securities": table_frame("securities", [security(c, first) for c, first in codes.items()], source="test"),
            "bars": bar_frame([row for code in codes for row in bars(code)]),
            "adj_factor": table_frame("adj_factor", factors),
            "daily_status": table_frame("daily_status", statuses, source="test"),
        },
    )
    return store


def bar_frame(rows: list[dict]):
    return table_frame("bars", rows, amount=1e6, preclose_derived=False, source="test")


SZ_FACTORS = [{"code": SZ, "ex_date": DAYS[0], "factor": 1.0}, {"code": SZ, "ex_date": DAYS[SZ_EX_DATE], "factor": 1.1}]
SZ_STATUS = [{"code": SZ, "date": day, "tradestatus": 1, "is_st": t >= 4} for t, day in enumerate(DAYS)]


def main_store(home) -> Store:
    """600000.SH and 000001.SZ from D0; 000001.SZ has an ex-date on D3 and is ST on D4 and D5."""
    return seed(home, {SH: 0, SZ: 0}, SZ_FACTORS, SZ_STATUS)


@pytest.fixture(scope="session")
def load(tmp_path_factory):
    """load_panel on the main store, with its own cache directory."""
    root = tmp_path_factory.mktemp("expr")
    return partial(load_panel, store=main_store(root / "home"), cache_dir=root / "cache")


@pytest.fixture(scope="session")
def panel(load):
    return load()


@pytest.fixture
def two_versions(tmp_path):
    """The main store's panel at version 1 and at version 2, where D4 and D5 prices double and 600000.SH splits."""
    store = main_store(tmp_path / "home")
    factors = [
        *SZ_FACTORS,
        {"code": SH, "ex_date": DAYS[0], "factor": 1.0},
        {"code": SH, "ex_date": DAYS[5], "factor": 2.0},
    ]
    store.commit(
        {"source": "test"},
        replace={
            "bars": bar_frame([row for code in (SH, SZ) for row in bars(code, doubled=(4, 5))]),
            "adj_factor": table_frame("adj_factor", factors),
        },
    )
    load_version = partial(load_panel, store=store, cache_dir=tmp_path / "cache")
    return load_version(version=1), load_version(version=2)


@pytest.fixture(scope="session")
def free_panel(tmp_path_factory):
    """000001.SZ as in the main store but without factors or daily_status, and 000002.SZ, new on D1 and so FREE."""
    root = tmp_path_factory.mktemp("free")
    return load_panel(store=seed(root / "home", {SZ: 0, SZ2: 1}, [], []), cache_dir=root / "cache")
