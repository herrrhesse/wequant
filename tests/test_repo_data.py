"""D20: the repository holds no market data. A tracked data file above LIMIT bytes fails unless allowed here."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 4096  # bytes: room for a small synthetic sample, none for a table
DATA = {".json", ".jsonl", ".csv", ".tsv", ".parquet", ".feather", ".arrow", ".xls", ".xlsx", ".npy", ".npz", ".pkl"}
DATA |= {".h5", ".toml"}
CONFIG = {"pyproject.toml", "package-lock.json"}  # files that configure or pin dependencies, not tables
ALLOWED = {
    "frontend/contract/service-models.schema.json",  # the service models' JSON Schema, not data
    "tests/data/resolutions_sample.toml",  # synthetic
    "wequant/rules/rules.toml",  # dated trading rules, written from the exchanges' published rules
    "tests/data/rule_examples.toml",  # synthetic, worked out by hand from rules.toml
}


def tracked() -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [path for path in done.stdout.split("\0") if path]


def test_no_tracked_data_file_is_larger_than_a_sample():
    paths = [p for p in tracked() if Path(p).suffix in DATA and Path(p).name not in CONFIG and (ROOT / p).exists()]
    large = sorted(p for p in paths if (ROOT / p).stat().st_size > LIMIT and p not in ALLOWED)
    assert not large, f"data files above {LIMIT} bytes (D20: no market data in the repository): {large}"
