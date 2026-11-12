"""The data root's settings: <home>/settings.json. They hold the run archive's size limit, how many versions cleanup
keeps, the version requests read when they name none, and the versions and runs the user pinned. A value that is not
valid is reported and read as its default; without the file every setting is its default."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wequant.data.files import atomic_write
from wequant.data.store import exclusive
from wequant.runs.archive import ID

FILE = "settings.json"
LOCK = ".settings.lock"


def _whole(value: object, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _run_id(value: object) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None


CHECKS = {  # setting -> (valid value, what it must be)
    "runs_limit_gb": (lambda v: isinstance(v, int | float) and not isinstance(v, bool) and v > 0, "a number above 0"),
    "keep_versions": (lambda v: _whole(v, 1), "a whole number from 1"),
    "version_in_use": (lambda v: v is None or _whole(v, 1), "a version number or null"),
    "pinned_versions": (lambda v: isinstance(v, list) and all(_whole(n, 1) for n in v), "a list of version numbers"),
    "pinned_runs": (lambda v: isinstance(v, list) and all(_run_id(r) for r in v), "a list of run ids"),
}


@dataclass(frozen=True)
class Settings:
    """Every setting, each its default unless the file gives another."""

    runs_limit_gb: float = 2.0  # the run archive's size limit; the oldest unpinned runs go beyond it
    keep_versions: int = 5  # versions cleanup keeps besides the pinned, the one in use and those pinned runs read
    version_in_use: int | None = None  # what requests without a data_version read; None: the newest
    pinned_versions: tuple[int, ...] = ()
    pinned_runs: tuple[str, ...] = ()


def load(home: Path) -> tuple[Settings, list[str]]:
    """The settings of the data root and why any value was read as its default."""
    path = Path(home) / FILE
    if not path.exists():
        return Settings(), []
    try:
        data = json.loads(path.read_text("utf-8"))
    except ValueError as error:
        return Settings(), [f"{FILE} is not JSON ({error}); every setting reads as its default"]
    if not isinstance(data, dict):
        return Settings(), [f"{FILE} is not a JSON object; every setting reads as its default"]
    values, problems = {}, []
    for key, value in data.items():
        if key not in CHECKS:
            problems.append(f"{key} is not a setting; it is ignored")
        elif CHECKS[key][0](value):
            values[key] = tuple(value) if isinstance(value, list) else value
        else:
            problems.append(f"{key} = {value!r} is not {CHECKS[key][1]}; it reads as {getattr(Settings(), key)!r}")
    return Settings(**values), problems


def change(home: Path, edit: Callable[[Settings], dict]) -> Settings:
    """Write the settings with the changes edit gives for the settings as they are, under the settings lock; the
    settings now."""
    with exclusive(Path(home) / LOCK):
        settings = load(home)[0]
        settings = dataclasses.replace(settings, **edit(settings))
        data = {key: list(v) if isinstance(v, tuple) else v for key, v in dataclasses.asdict(settings).items()}
        atomic_write(Path(home) / FILE, json.dumps(data, indent=1).encode("utf-8") + b"\n")
        return settings


def pinned(values: tuple, value: object, pin: bool) -> tuple:
    """values with value added (pin) or removed, in order."""
    return tuple(sorted({*values, value})) if pin else tuple(v for v in values if v != value)
