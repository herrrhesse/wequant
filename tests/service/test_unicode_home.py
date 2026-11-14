"""A data root whose path has Chinese characters and spaces works like any other (round 3, W0), e.g.
C:\\Users\\张三\\WeQuant 数据 on Windows."""

from tests.backtest.market import build_store
from tests.service.conftest import DAYS, MARKET, SCREENED
from wequant import service
from wequant.service import Context


def test_a_data_root_named_in_chinese_with_spaces_reads_writes_and_exports(tmp_path, cache):
    home = build_store(tmp_path / "张三 的 WeQuant 数据", DAYS, MARKET).home
    context = Context(home, cache)
    run = service.backtest(service.BacktestRequest(strategy=SCREENED, benchmark=None), context)
    pool = service.pools_save(service.PoolSaveRequest(name="我的 自选", codes=["000001.SZ"]), context)
    exported = service.runs_export(service.ExportRunRequest(id=run.run_id), context)
    assert service.runs_show(service.RunRequest(id=run.run_id), context).id == run.run_id
    assert service.pools_show(service.PoolRequest(id=pool.id), context).name == "我的 自选"
    assert exported.path.startswith(str(home))
