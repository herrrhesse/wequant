import shutil

from tools.investment_data.run import run_import
from tools.investment_data.tests import synthetic as syn
from wequant.data.schema import TABLES
from wequant.data.store import Store


def test_run_import_end_to_end_is_idempotent(tmp_path):
    home = tmp_path / "home"
    raw = home / "raw" / "investment_data" / syn.TAG
    syn.write_release(raw, tmp_path / "src")
    code, report = run_import(syn.TAG, home)
    assert code == 0, report.read_text()
    assert "Re-import of the same input:** PASS" in report.read_text()
    manifest = Store(home).manifest()
    assert manifest["version"] == 1 and manifest["release_tag"] == syn.TAG
    assert manifest["last_trade_date"] == str(syn.DAYS[-1])
    assert set(manifest["tables"]) == set(TABLES) - {"daily_status"}
    assert run_import(syn.TAG, home, check_idempotency=False)[0] == 0
    shutil.rmtree(raw / "extracted")
    assert run_import(syn.TAG, home, check_idempotency=False)[0] == 0  # re-extracted: same data
    assert Store(home).manifest()["version"] == 1
