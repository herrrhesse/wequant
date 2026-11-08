"""One import run: download, verify, extract, convert, commit, re-import to check idempotency, report."""

from __future__ import annotations

import time
from pathlib import Path

from tools.investment_data import release, report
from tools.investment_data.convert import ConventionError, convert
from wequant.data.schema import TABLES
from wequant.data.store import Store, wequant_home


def run_import(tag: str = "latest", home: Path | None = None, check_idempotency: bool = True) -> tuple[int, Path]:
    """Returns (exit code, report path). Exit codes: 0 imported, 2 verification failed, 3 conventions broken."""
    home = Path(home) if home is not None else wequant_home()
    store = Store(home)
    tag = release.resolve_tag(tag)
    raw_dir = home / "raw" / "investment_data" / tag
    report_path = home / "reports" / f"seed_import_{tag}.md"
    run: dict = {"tag": tag, "home": str(home), "timings": {}}
    stats: dict = {}
    clock = time.perf_counter()

    def step(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        run["timings"][name] = now - clock
        clock = now

    try:
        run["download"] = release.download(tag, raw_dir)
        step("download")
        run["verification"] = release.verify(tag, raw_dir)
        step("verify archive")
        run["extraction"] = release.extract(raw_dir)
        step("extract")
        tables, stats = convert(raw_dir / "extracted", tag)
        step("convert")
        meta = {"last_trade_date": stats["last_trade_date"], "source": "investment_data", "release_tag": tag}
        run["commit"] = store.commit(meta, replace=tables)
        step("commit")
        if check_idempotency:
            del tables
            again = store.commit(meta, replace=convert(raw_dir / "extracted", tag)[0])
            run["repeat"] = {
                "same_hashes": again.hashes == run["commit"].hashes,
                "changed": again.changed,
                "files": sum(len(parts) for parts in again.hashes.values()),
            }
            step("re-import")
        run["manifest"] = store.manifest()
        run["disk"] = _disk(home, raw_dir, run["manifest"])
        run["status"], code = "imported", 0
    except release.VerificationError as exc:
        run["status"], code = f"STOPPED: verification failed: {exc}", 2
    except ConventionError as exc:
        step("convert")
        run["status"], code = "STOPPED: the data breaks an expected convention", 3
        run["problems"], stats = exc.problems, exc.stats
    except Exception as exc:
        run["status"] = f"FAILED: {type(exc).__name__}: {exc}"
        _write(report_path, run, stats)
        raise
    _write(report_path, run, stats)
    return code, report_path


def _write(path: Path, run: dict, stats: dict) -> None:
    run["timings"]["total"] = sum(v for k, v in run["timings"].items() if k != "total")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.render(run, stats), "utf-8")


def _disk(home: Path, raw_dir: Path, manifest: dict) -> dict:
    def size(path: Path) -> int:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0

    return {
        "archive": (raw_dir / release.ARCHIVE).stat().st_size,
        "extracted": size(raw_dir / "extracted"),
        "tables": sum((home / e["path"]).stat().st_size for t in manifest["tables"].values() for e in t["files"]),
        "tables_on_disk": sum(size(home / name) for name in TABLES),
    }
