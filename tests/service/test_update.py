"""data update: the daily update's result and its failures, against a fake BaoStock. Synthetic data only."""

from types import SimpleNamespace

import pytest

from tests.update_fake import AFTER_CLOSE, D4, Market, rows, seed_store
from wequant.data.raw import BaoStockRaw
from wequant.data.store import Store, exclusive
from wequant.service import Context, Exit, ServiceError, UpdateRequest, data_update


@pytest.fixture
def home(tmp_path):
    return seed_store(tmp_path / "home").home


def updated(home, monkeypatch, market) -> object:
    monkeypatch.setattr("wequant.data.sources.baostock.import_baostock", lambda: market)
    return data_update(UpdateRequest(), Context(home, clock=lambda: AFTER_CLOSE[D4].isoformat()))


def failure(home, monkeypatch, market) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        updated(home, monkeypatch, market)
    assert Store(home).versions() == [1]  # the store is as it was
    return caught.value


def test_the_result_names_the_new_version_and_says_what_came_in(home, monkeypatch):
    result = updated(home, monkeypatch, Market(days={D4: rows(D4)}))
    assert (result.ok, result.status, result.data_version, result.previous_version, result.days) == (
        True,
        "updated",
        2,
        1,
        [D4],
    )
    assert result.text == "更新完成：新增 1 个交易日，数据到 2024-01-05"


def test_a_lost_connection_is_unreachable(home, monkeypatch):
    error = failure(home, monkeypatch, Market(days={D4: rows(D4)}, fail_on="query_daily_history_k_AStock"))
    assert (error.exit, [d.code for d in error.detail]) == (Exit.UNREACHABLE, ["source_unreachable"])


def test_a_refused_login_is_unreachable(home, monkeypatch):
    refused = Market()
    refused.login = lambda: SimpleNamespace(error_code="10001001", error_msg="用户未登录")
    assert failure(home, monkeypatch, refused).exit == Exit.UNREACHABLE


def test_another_baostock_session_makes_it_wait(home, monkeypatch):
    raw = BaoStockRaw.at(home)
    raw.root.mkdir(parents=True)
    with exclusive(raw.root / ".lock"):
        error = failure(home, monkeypatch, Market(days={D4: rows(D4)}))
    assert (error.exit, [d.code for d in error.detail]) == (Exit.LOCKED, ["store_busy"])
