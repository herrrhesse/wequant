"""A rebalance run through the archive: saved, then re-run byte for byte by run_form in fresh processes."""

import os
import subprocess
import sys
from pathlib import Path

from tests.backtest.market import CSI300, ROTATION, WEEK, build_store
from wequant.account import Execution
from wequant.backtest import Backtest, panel_for, planned_id, run_form
from wequant.expr import REF, Field
from wequant.runs import Archive
from wequant.strategy import Factor, Strategy

REPO = Path(__file__).resolve().parents[2]
ADJ = Field("adj_close")
ROTATE = Strategy(factors=(Factor("ret1", ADJ / REF(ADJ, 1)),), top_n=1, period="day")
BACKTEST = Backtest(ROTATE, cash=100_000, execution=Execution(slippage_bps=0.0))


def test_a_rebalance_run_reruns_byte_for_byte_in_fresh_processes(tmp_path):
    home, cache, runs = tmp_path / "home", tmp_path / "cache", tmp_path / "runs"
    store = build_store(home, WEEK, ROTATION, index=CSI300)
    run = run_form(BACKTEST.form, 1, store=store, cache_dir=cache)
    assert run.ledger.orders.height == 5 and "benchmark" in run.ledger.daily.columns
    assert Archive(runs).save(run) == run.id
    assert planned_id(BACKTEST, panel_for(BACKTEST, store=store, version=1, cache_dir=cache)) == run.id
    script = (
        "import functools; from wequant.backtest import run_form; from wequant.data.store import Store; "
        "from wequant.runs import Archive; "
        f"engine = functools.partial(run_form, store=Store({str(home)!r}), cache_dir={str(cache)!r}); "
        f"print(Archive({str(runs)!r}).rerun({run.id!r}, engine).identical)"
    )
    for seed in ("1", "2"):
        env = os.environ | {"PYTHONPATH": str(REPO), "PYTHONHASHSEED": seed}
        done = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env, capture_output=True, text=True)
        assert (done.returncode, done.stdout) == (0, "True\n"), done.stderr
