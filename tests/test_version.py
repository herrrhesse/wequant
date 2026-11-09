import re
import subprocess
from importlib.metadata import version

import pytest

import wequant.version
from wequant.version import code_version


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A one-commit git checkout that code_version reads in place of this repo."""
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init"], check=True)
    monkeypatch.setattr(wequant.version, "REPO", tmp_path)
    return tmp_path


def test_code_version_starts_with_the_package_version():
    assert code_version().startswith(version("wequant"))


def test_untracked_code_makes_the_version_dirty(checkout):
    (checkout / "notes.md").touch()
    assert re.fullmatch(re.escape(version("wequant")) + r"\+g[0-9a-f]{12}", code_version())
    (checkout / "wequant").mkdir()
    (checkout / "wequant" / "new.py").touch()
    assert code_version().endswith(".dirty")


def test_an_enclosing_checkout_is_not_the_code_version(checkout, monkeypatch):
    (checkout / "site-packages").mkdir()
    monkeypatch.setattr(wequant.version, "REPO", checkout / "site-packages")
    assert code_version() == version("wequant")
