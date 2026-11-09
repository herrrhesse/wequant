"""The code version that store commits and runs record."""

from __future__ import annotations

import subprocess
from importlib.metadata import version
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODE = ("wequant", "tools", "pyproject.toml", "uv.lock")  # what a run executes; other edits leave it clean


def code_version() -> str:
    """<package version>+g<commit>[.dirty] inside a git checkout, else the package version."""
    base = version("wequant")
    if not (REPO / ".git").exists():  # an installed copy: git would report whatever checkout encloses it
        return base
    try:
        commit = _git("rev-parse", "--short=12", "HEAD")
        dirty = _git("status", "--porcelain", "--", *CODE)
    except (OSError, subprocess.CalledProcessError):
        return base
    return f"{base}+g{commit}" + (".dirty" if dirty else "")


def _git(*args: str) -> str:
    done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()
