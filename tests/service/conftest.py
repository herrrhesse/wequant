import pytest

from tests.backtest.market import build_store, business_days
from tests.test_panel import BARS, bar, commit, seed
from tests.test_rules_tables import META, tables_v1
from wequant.cli import main
from wequant.data.store import Store
from wequant.service import Context
from wequant.strategy import Strategy


@pytest.fixture(autouse=True)
def no_real_home(monkeypatch, tmp_path):
    """A forgotten --home or Context home can never reach real data."""
    monkeypatch.setenv("WEQUANT_HOME", str(tmp_path / "unset"))


@pytest.fixture
def home(tmp_path):
    """tests.test_panel's store at v1: every day status, B's free listing days, a voided day, 7 data_issues rows."""
    return seed(tmp_path / "home").home


@pytest.fixture
def home_v2(home):
    """home with a version 2 in which 600000.SH closes at 10.10 instead of 10.00 on 2024-01-03."""
    old = bar("600000.SH", 2, 10000, 9800)
    commit(Store(home), bars=[row for row in BARS if row != old] + [bar("600000.SH", 2, 10100, 9800)])
    return home


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def context(home, cache):
    return Context(home, cache)


@pytest.fixture
def limits_home(tmp_path):
    """tests.test_rules_tables' store at v1: one breach found and not recorded."""
    store = Store(tmp_path / "limits")
    store.commit(META, replace=tables_v1())
    return store.home


@pytest.fixture
def cli(capsys):
    """run(*argv) -> (exit code, stdout, stderr) of the wequant command."""

    def run(*argv):
        with pytest.raises(SystemExit) as exited:
            main([str(arg) for arg in argv])
        out, err = capsys.readouterr()
        return exited.value.code, out, err

    return run


MOM = {"factors": [{"name": "mom", "expr": ["DIV", "close", ["REF", "close", 5]]}], "top_n": 2}


@pytest.fixture
def library_home(tmp_path):
    """A data root whose strategy s has versions 1 (MOM) and 2 (MOM with top_n 3)."""
    home = tmp_path / "library"
    library = Context(home).library()
    library.save(Strategy.from_form(MOM), "s", note="first")
    library.save(Strategy.from_form({**MOM, "top_n": 3}), "s")
    return home


DAYS = business_days(8)  # 2024-01-02 .. 2024-01-11, weekdays
MARKET = {  # (open, close) per day; None: no bar
    "000001.SZ": [(10.0, 10.0 + 0.2 * t) for t in range(8)],  # steady riser
    "000002.SZ": [(12.0, 12.0 + 0.1 * t) for t in range(7)] + [None],  # no bar on the last day
    "300001.SZ": [(35.0, 35.0 + 0.1 * t) for t in range(8)],  # too dear for "cheap"
    "600000.SH": [(20.0, 20.0 + 0.5 * t) for t in range(7)] + [(23.0, 22.0)],  # falls on the last day
    "600004.SH": [(8.0, 8.0 + 0.3 * t) for t in range(8)],
    "600006.SH": [(15.0, 15.0 + 0.4 * t) for t in range(8)],
    "830799.BJ": [(5.0, 5.0 + 0.1 * t) for t in range(8)],
}
SCREENED = {  # cheap < 30, rising today; momentum high, level low; top 2 of 3 capped at 40%
    "filters": [
        {"name": "cheap", "expr": ["GT", 30.0, "close"]},
        {
            "name": "trend",
            "conditions": [
                {"name": "liquid", "expr": ["GT", "volume", 0.0]},
                {"name": "rising", "expr": ["GT", "close", ["REF", "close", 1]]},
            ],
        },
    ],
    "factors": [
        {"name": "mom", "expr": ["DIV", "close", ["REF", "close", 3]]},
        {"name": "level", "expr": "close", "prefer": "low"},
    ],
    "top_n": 2,
    "backups": 1,
    "max_weight": 0.4,
}


@pytest.fixture
def market_home(tmp_path):
    """MARKET at version 1, with the CSI 300 rising 1% a day, and strategy m saved as SCREENED."""
    store = build_store(tmp_path / "market", DAYS, MARKET, index=("000300.SH", [3000 * 1.01**t for t in range(8)]))
    Context(store.home).library().save(Strategy.from_form(SCREENED), "m")
    return store.home
