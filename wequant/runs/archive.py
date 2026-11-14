"""Runs kept on disk, one write-once directory per run id."""

from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path

import polars as pl

from wequant.canonical import canonical
from wequant.data.files import atomic_write, remove_tree, rename
from wequant.data.store import wequant_home
from wequant.runs.compare import SAME, Comparison, code_notes, differences
from wequant.runs.record import FILES, PROVENANCE, provenance, read_json, read_run, run_files
from wequant.runs.run import Run

RUNS = "runs"
ID = re.compile(r"[0-9a-f]{16}")
Engine = Callable[[dict, int], Run]  # (description, store version) -> the run; the caller binds store and caches
HEADLINE = ("total_return", "annual_return", "sharpe", "max_drawdown")
INDEX = {
    "id": pl.String,
    "engine": pl.String,
    "store_version": pl.Int64,
    "data_key": pl.String,
    "anchor": pl.Date,
    "start": pl.Date,
    "end": pl.Date,
    "description": pl.String,
    **dict.fromkeys(HEADLINE, pl.Float64),
    "code_version": pl.String,
    "created_at": pl.String,
    "store_version_name": pl.String,  # as noted at save; null for runs archived before names were noted
}


class RunConflict(RuntimeError):
    """A run whose id is archived with other results."""

    def __init__(self, run_id: str, differences: dict[str, str], directory: Path) -> None:
        super().__init__(run_id, differences, directory)  # every argument, so the error pickles across processes
        self.run_id, self.differences, self.directory = run_id, differences, directory

    def __str__(self) -> str:
        names = ", ".join(self.differences)
        return f"run {self.run_id} is archived with other results ({names}); remove {self.directory} to replace it"


class Archive:
    """Runs under root, each in root/<id>: saved whole or not at all, never overwritten."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else wequant_home() / RUNS

    def save(self, run: Run, notes: dict | None = None) -> str:
        """Archive run and return its id; a run already archived is checked against its content and kept. notes go
        into provenance.json, which reruns never compare (e.g. the store version's display name)."""
        files, directory = run_files(run), self.path(run.id)
        if not directory.exists():
            self._write(directory, files | {PROVENANCE: provenance(run, notes)})
        found = {name: text for name, text in differences(_archived(directory), files).items() if text != SAME}
        if found:
            raise RunConflict(run.id, found, directory)
        return run.id

    def files(self, run_id: str) -> dict[str, bytes]:
        """Every file of the archived run, provenance.json included, by name; KeyError when there is none."""
        directory = self._directory(run_id)
        return {name: (directory / name).read_bytes() for name in (*FILES, PROVENANCE)}

    def add(self, run_id: str, files: dict[str, bytes]) -> bool:
        """Archive a run's files made elsewhere under its id; False when the run is already archived with the same
        content, RunConflict when with other content, ValueError for files that do not read as that run."""
        if set(files) != {*FILES, PROVENANCE}:
            raise ValueError(f"a run's files are {sorted({*FILES, PROVENANCE})}, not {sorted(files)}")
        directory = self.path(run_id)
        if directory.exists():
            compared = {name: files[name] for name in FILES}  # provenance.json is never compared
            found = {name: text for name, text in differences(_archived(directory), compared).items() if text != SAME}
            if found:
                raise RunConflict(run_id, found, directory)
            return False
        self._write(directory, files)
        try:
            read_run(directory)  # the files read as a run of this format, under this id
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return True

    def delete(self, run_id: str) -> None:
        """Remove the archived run; KeyError when there is none."""
        remove_tree(self._directory(run_id))

    def size(self, run_id: str) -> int:
        """Bytes the archived run takes on disk."""
        return sum(path.stat().st_size for path in self._directory(run_id).iterdir())

    def notes(self, run_id: str) -> dict:
        """The run's provenance.json: code and library versions, creation time and the notes it was saved with."""
        return read_json(self._directory(run_id) / PROVENANCE)

    def load(self, run_id: str) -> Run:
        """The archived run; KeyError when there is none."""
        return read_run(self._directory(run_id))

    def row(self, run_id: str) -> dict:
        """The run's INDEX row by column; KeyError when there is none."""
        return dict(zip(INDEX, _row(self._directory(run_id))))

    def path(self, run_id: str) -> Path:
        """The run's directory; ValueError for a name that is not a run id."""
        if not (isinstance(run_id, str) and ID.fullmatch(run_id)):
            raise ValueError(f"{run_id!r} is not a run id: 16 lowercase hex characters")
        return self.root / run_id

    def __contains__(self, run_id: object) -> bool:
        """True for the id of an archived run; never raises."""
        return isinstance(run_id, str) and ID.fullmatch(run_id) is not None and os.path.isdir(self.root / run_id)

    def index(self) -> pl.DataFrame:
        """One INDEX row per archived run, by created_at then id."""
        rows = [_row(directory) for directory in self._runs()]
        return pl.DataFrame(rows, schema=INDEX, orient="row").sort("created_at", "id")

    def compare(self, run_id: str, run: Run) -> Comparison:
        """run's files against the archived bytes."""
        directory = self._directory(run_id)
        then = read_json(directory / PROVENANCE)["code_version"]
        found = differences(_archived(directory), run_files(run))
        return Comparison(run_id, run, found, code_notes(then, run.code_version))

    def rerun(self, run_id: str, engine: Engine) -> Comparison:
        """Run the archived description again on its store version and compare."""
        old = self.load(run_id)
        return self.compare(run_id, engine(old.description, old.store_version))

    def _runs(self) -> list[Path]:
        """The run directories under root, by name; every other entry is ignored."""
        if not self.root.is_dir():
            return []
        return sorted(path for path in self.root.iterdir() if ID.fullmatch(path.name) and path.is_dir())

    def _directory(self, run_id: str) -> Path:
        directory = self.path(run_id)
        if not directory.is_dir():
            raise KeyError(f"no run {run_id} in {self.root}")
        return directory

    def _write(self, directory: Path, files: dict[str, bytes]) -> None:
        """Write files into a hidden directory, then rename it to directory; another process may win the rename."""
        tmp = self.root / f".{directory.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            for name, data in files.items():
                atomic_write(tmp / name, data)
            rename(tmp, directory)
            _sync(self.root)
        except OSError:
            if not directory.is_dir():
                raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _sync(directory: Path) -> None:
    """Make a rename inside directory survive a power loss (POSIX; Windows cannot open directories)."""
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _archived(directory: Path) -> dict[str, bytes]:
    """The compared files directory holds, by name."""
    return {path.name: path.read_bytes() for path in map(directory.joinpath, FILES) if path.is_file()}


def _row(directory: Path) -> tuple:
    """The INDEX row of the run in directory; ValueError naming it when it is not a readable run."""
    try:
        doc, made = read_json(directory / "run.json"), read_json(directory / PROVENANCE)
        metrics, inputs = read_json(directory / "metrics.json")["metrics"], doc["inputs"]
        days = [dt.date.fromisoformat(day) for day in (doc["anchor"], doc["ledger"]["start"], doc["end"])]
        if doc["id"] != directory.name:
            raise ValueError(f"run.json has id {doc['id']}")
        return (
            directory.name,
            inputs["engine"],
            inputs["store_version"],
            inputs["data_key"],
            *days,
            canonical(inputs["description"]),
            *(metrics[name]["value"] for name in HEADLINE),
            made["code_version"],
            made["created_at"],
            made.get("store_version_name"),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{directory} is not a readable run: {error!r}") from error
