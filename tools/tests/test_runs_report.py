"""The run archive report on the golden store: every check passes, and the archive is the only thing written."""

import re

import pytest

from tests.account.golden import build_store
from tools import runs_report
from wequant.runs import Archive


def listing(home):
    return sorted((p.relative_to(home).as_posix(), p.stat().st_size) for p in home.rglob("*"))


def test_report_passes_every_check_and_writes_only_the_archive(tmp_path):
    home, runs, out = tmp_path / "home", tmp_path / "runs", tmp_path / "runs.md"
    build_store(home)
    before = listing(home)
    paths = ["--home", str(home), "--cache", str(tmp_path / "cache"), "--runs", str(runs), "--out", str(out)]
    options = ["--years", "1", "--top", "1", "--window", "3", "--benchmark", "none"]
    assert runs_report.main([*paths, *options]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("# Run archive report: store v1")
    headings = ("The run", "Save and load", "Re-run in this process", "Re-run in a fresh process", "A changed result")
    for number, heading in enumerate([*headings, "Index"], 1):
        assert f"\n## {number}. {heading}\n" in text
    assert "**Re-run:**" in text and "PASS: every file byte-identical" in text
    assert re.search(r"\*\*Result:\*\* [\d.]+ s, exit 0; PASS: identical", text)
    assert "FAIL" not in text and text.count("PASS") == 7
    assert re.search(r"peak RSS: [\d,]+ MB \(PASS", text)
    assert listing(home) == before
    [directory] = runs.iterdir()
    run = Archive(runs).load(directory.name)
    assert run.engine == "rebalance" and run.ledger.orders.height > 0 and run.picks.height > 0
    assert run.description["benchmark"] is None and run.description["start"] == "2024-03-01"


def test_the_archive_may_not_lie_inside_the_store_home(tmp_path, capsys):
    home = tmp_path / "home"
    build_store(home)
    with pytest.raises(SystemExit) as stopped:
        runs_report.main(["--home", str(home), "--runs", str(home / "runs")])
    assert stopped.value.code == 2 and "lies inside the store home" in capsys.readouterr().err
    assert not (home / "runs").exists()
