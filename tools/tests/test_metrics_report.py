from pathlib import Path

import pytest

from tests.frames import table_frame
from tools import metrics_report
from wequant.data.store import Store

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
CLOSES = {
    "000300.SH": [900_000, 1_000_000, 1_100_000, 990_000, 990_000, 1_188_000],
    "000905.SH": [5_000_000, 5_000_000, 5_250_000, 4_620_000, 4_666_200, 5_132_820],
    "000852.SH": [6_000_000, 6_000_000, 6_300_000, 5_544_000, None, 6_159_384],
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    bars = [
        {"code": code, "date": day, "open": close, "high": close, "low": close, "close": close}
        for code, closes in CLOSES.items()
        for day, close in zip(DAYS, closes)
        if close is not None
    ]
    Store(tmp_path).commit(
        {"source": "test"},
        replace={
            "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
            "index_bars": table_frame("index_bars", bars, volume=0, amount=0.0, source="test"),
        },
    )
    return tmp_path


def snapshot(home: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(home.rglob("*")) if path.is_file()}


def test_report_of_a_synthetic_store(home: Path, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "metrics.md"
    before = snapshot(home)
    assert metrics_report.main(["--home", str(home), "--base", "2024-01-03", "--out", str(out)]) == 0
    assert snapshot(home) == before
    text = out.read_text("utf-8")
    assert text.startswith("# Metrics of a 000300.SH buy-and-hold\n\n- **Store:** version 1; code_version ")
    for heading in ("## 1. Metrics", "## 2. Checks", "## 3. metrics.json against 000905.SH"):
        assert f"\n{heading}\n" in text
    assert "held to 2024-01-09" in text and "bought at the close of 2024-01-03" in text
    assert "| trading_days | days | 4 | 4 |" in text
    assert "| total_return | fraction | 0.188000 | 0.188000 |" in text
    assert "| max_drawdown_peak | date | 2024-01-04 | 2024-01-04 |" in text
    assert "| max_drawdown_recovery | date | 2024-01-09 | 2024-01-09 |" in text
    assert "| benchmark_missing_days | days | 0 | 2 |" in text
    assert "| turnover | fraction | null (no_trade_values) | null (no_trade_values) |" in text
    assert "| capacity_max | fraction | null (no_orders) | null (no_orders) |" in text
    assert "**000852.SH:** days without a bar: 1 (2024-01-08)" in text
    assert '"max_drawdown_trough": {\n   "value": "2024-01-05",' in text


def test_report_goes_to_stdout(home: Path, capsys):
    assert metrics_report.main(["--home", str(home), "--base", "2024-01-02"]) == 0
    assert "| trading_days | days | 5 | 5 |" in capsys.readouterr().out
