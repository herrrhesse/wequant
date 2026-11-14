"""Where wequant keeps things by default on each system (round 3, W0), and numba's compile cache."""

import os

import numba
import pytest

from wequant import paths

CONFIG_DIR = paths.config_dir  # the real one: conftest gives each test a folder of its own


@pytest.mark.parametrize("windows", [False, True], ids=["linux", "windows"])
def test_each_system_keeps_data_settings_and_caches_in_its_own_places(monkeypatch, tmp_path, windows):
    """Windows: data and caches under %LOCALAPPDATA%\\WeQuant, settings under %APPDATA%\\WeQuant."""
    local, roaming, home = tmp_path / "Local", tmp_path / "Roaming", tmp_path / "me"
    for variable, value in {"LOCALAPPDATA": local, "APPDATA": roaming, "HOME": home, "USERPROFILE": home}.items():
        monkeypatch.setenv(variable, str(value))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "conf"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "caches"))
    monkeypatch.setattr(paths, "WINDOWS", windows)
    monkeypatch.setattr(paths, "config_dir", CONFIG_DIR)
    found = (paths.default_home(), paths.config_dir(), paths.cache_dir())
    if windows:
        assert found == (local / "WeQuant" / "data", roaming / "WeQuant", local / "WeQuant" / "cache")
    else:
        assert found == (home / ".wequant", home / "conf" / "wequant", home / "caches" / "wequant")


def test_numba_caches_in_the_user_s_cache_folder_unless_told_otherwise(monkeypatch, tmp_path):
    monkeypatch.delenv("NUMBA_CACHE_DIR", raising=False)
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(numba.config, "CACHE_DIR", "")
    paths.use_user_cache()
    assert os.environ["NUMBA_CACHE_DIR"] == numba.config.CACHE_DIR == str(tmp_path / "numba")
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path / "mine"))
    paths.use_user_cache()
    assert os.environ["NUMBA_CACHE_DIR"] == str(tmp_path / "mine")
