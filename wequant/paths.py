"""Where wequant keeps things by default on each system (round 3, W0): the data root, the user's settings that are not
the data root's (which data root to use, the setup's progress) and caches the program can rebuild.

Linux and macOS keep the old places (~/.wequant, and the XDG config and cache folders); Windows uses WeQuant folders
under %LOCALAPPDATA% (data, caches) and %APPDATA% (settings), so nothing is written next to the program."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "WeQuant"
WINDOWS = os.name == "nt"


def default_home() -> Path:
    """The data root when nothing names one."""
    if WINDOWS:
        return _windows("LOCALAPPDATA") / APP / "data"
    return Path("~/.wequant").expanduser()


def config_dir() -> Path:
    """The user's wequant settings that live outside any data root."""
    if WINDOWS:
        return _windows("APPDATA") / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "wequant"


def cache_dir() -> Path:
    """Caches the program rebuilds when they are gone, e.g. numba's compiled operators."""
    if WINDOWS:
        return _windows("LOCALAPPDATA") / APP / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or "~/.cache").expanduser() / "wequant"


def absolute(path: str | Path) -> Path:
    """path with ~ expanded, made absolute and normalized (no "." or ".."), symbolic links left as they are (F-32)."""
    return Path(os.path.abspath(Path(path).expanduser()))


def use_user_cache() -> None:
    """Point numba's compile cache at cache_dir() unless NUMBA_CACHE_DIR names one, so a program installed where it
    cannot write (C:\\Program Files) still compiles its operators only once; the package does it on import."""
    if os.environ.get("NUMBA_CACHE_DIR"):
        return
    os.environ["NUMBA_CACHE_DIR"] = str(cache_dir() / "numba")
    if "numba" in sys.modules:  # numba has read its settings already: set them where it reads them
        sys.modules["numba"].config.CACHE_DIR = os.environ["NUMBA_CACHE_DIR"]


def _windows(variable: str) -> Path:
    """A Windows profile folder from its variable, else its usual place under the user's profile."""
    found = os.environ.get(variable)
    if found:
        return Path(found)
    usual = {"LOCALAPPDATA": "AppData/Local", "APPDATA": "AppData/Roaming"}[variable]
    return Path.home() / usual
