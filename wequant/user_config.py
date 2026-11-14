"""The user's settings outside any data root (round 3, F-30): which data root to use, and how far the first-run setup
has got; one JSON file in wequant.paths.config_dir()."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from wequant import paths

FILE = "config.json"


def path() -> Path:
    return paths.config_dir() / FILE


def load() -> dict:
    """The settings; {} when there are none (or the file is unreadable: they are only remembered choices)."""
    try:
        found = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def update(**changes) -> dict:
    """The settings with changes written atomically; a change to None removes the key."""
    settings = {**load(), **changes}
    settings = {key: value for key, value in settings.items() if value is not None}
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return settings


def configured_home() -> Path | None:
    """The data root the user chose last, if any."""
    home = load().get("home")
    return Path(home) if isinstance(home, str) and home else None
