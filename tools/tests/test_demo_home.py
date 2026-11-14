"""The demo home: the end-to-end tests' synthetic data root (its one smoke test, plus determinism)."""

import polars as pl
import pytest

from tools.demo_home import build
from wequant import service
from wequant.data.store import Store
from wequant.service import Context


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    build(root / "home")
    return root


def files(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_same_seed_writes_the_same_bytes(demo):
    build(demo / "again")
    assert files(demo / "home") == files(demo / "again")


def test_a_screen_and_a_backtest_run_with_a_rejected_order_and_a_delisting(demo):
    context = Context(demo / "home", demo / "cache")
    saved = {"id": "demo-momentum"}
    assert service.screen(service.ScreenRequest(saved=saved), context).rows
    run = service.backtest(service.BacktestRequest(saved=saved, save=False), context)
    assert run.orders.rejected >= 1 and run.delisted


def test_the_layered_demo_home_holds_only_the_index_s_members_before_every_stock_s_layer(tmp_path):
    build(tmp_path / "layered", scope="layered")
    status = service.data_status(service.StatusRequest(), Context(tmp_path / "layered"))
    store = Store(tmp_path / "layered")
    bars, members = store.read_table("bars"), set(store.read_table("index_members")["code"])
    early = set(bars.filter(pl.col("date") < status.scope.layers[0].since)["code"])
    assert [layer.label for layer in status.scope.layers] == ["沪深全部", "沪深300"] and early == members
