import pytest

from tests.test_panel import BARS, bar, commit, seed
from tests.test_rules_tables import META, tables_v1
from wequant.cli import main
from wequant.data.store import Store
from wequant.service import Context


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
