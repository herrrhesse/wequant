"""Checks of a whole test session, for tests/ and tools/ alike."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
collect_ignore = ["frontend"]  # the frontend runs its own checks with npm
CACHES = {".hypothesis", ".pytest_cache", "__pycache__"}  # what pytest and hypothesis keep in the root
BEFORE = pytest.StashKey[set[str]]()


def pytest_sessionstart(session: pytest.Session) -> None:
    session.stash[BEFORE] = entries(session.config.rootpath)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the session if it left a new entry in the repository root: temporary files belong under tmp_path."""
    left = sorted(entries(session.config.rootpath) - session.stash[BEFORE] - CACHES)
    if left:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        reporter.write_line("")
        reporter.write_sep("=", f"the session left {', '.join(left)} in {session.config.rootpath}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def entries(root: Path) -> set[str]:
    return {path.name for path in root.iterdir()}
