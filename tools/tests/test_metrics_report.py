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
    assert text.startswith("# Metrics of a 000300.SH buy-and-hold") and "FAIL" not in text  # values: analytics tests
