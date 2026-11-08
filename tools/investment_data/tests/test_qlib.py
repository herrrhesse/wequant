import numpy as np
import pytest

from tools.investment_data.tests import synthetic as syn
from tools.investment_data import qlib


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    root = tmp_path_factory.mktemp("qlib") / "qlib_bin"
    syn.write_qlib(root)
    return root


def test_read_feature_returns_start_index_and_values(root):
    start, values = qlib.read_feature(root, "SZ000001", "close")
    assert start == 5 and values.dtype == np.float32 and values.size == syn.N_DAYS - 5
    assert qlib.read_calendar(root)[start] == syn.DAYS[5]


def test_read_instruments(root):
    spans = qlib.read_instruments(root / "instruments" / "all.txt")
    assert [s.code for s in spans] == [s.code for s in syn.STOCKS]
    assert spans[0].end == syn.DAYS[29]


def test_read_panel_flattens_all_spans(root):
    panel = qlib.read_panel(root, ("close", "volume"))
    assert panel.issues == []
    assert panel.code_idx.size == sum(s.end - s.start + 1 for s in syn.STOCKS)
    first = panel.code_idx == 4  # SZ000001 starts on day 5
    assert panel.cal_idx[first][0] == 5


def test_read_panel_skips_instruments_whose_files_disagree(tmp_path):
    root = tmp_path / "qlib_bin"
    syn.write_qlib(root)
    path = root / "features" / "sh600000" / "volume.day.bin"
    np.fromfile(path, dtype="<f4")[:-1].tofile(path)
    panel = qlib.read_panel(root, ("close", "volume"))
    assert len(panel.issues) == 1 and panel.issues[0].startswith("SH600000")


def test_to_wequant_keeps_the_exchange_prefix():
    assert qlib.to_wequant("SH600000") == "600000.SH"
    assert qlib.to_wequant("BJ920047") == "920047.BJ"
    for bad in ("sh600000", "SH60000", "SHT00018", "600000.SH"):
        with pytest.raises(ValueError):
            qlib.to_wequant(bad)
