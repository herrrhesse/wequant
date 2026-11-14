"""Which folder is the data root, changing it, and the first-run setup's progress (round 3, F-30). Each test has a
user settings folder of its own (conftest.py)."""

import shutil
from pathlib import Path

import pytest

from tests.test_panel import seed
from wequant import service, user_config
from wequant.data.store import Store, wequant_home
from wequant.service import Context, Exit, ServiceError
from wequant.service.labels import ERROR_CODES


def home_source(monkeypatch, tmp_path, argument=None, environment=None, chosen=None):
    """(the data root, where it comes from) under these settings."""
    if environment:
        monkeypatch.setenv("WEQUANT_HOME", str(environment))
    else:
        monkeypatch.delenv("WEQUANT_HOME", raising=False)
    user_config.update(home=str(chosen) if chosen else None)
    status = service.setup_status(service.SetupStatusRequest(), Context(argument))
    return Path(status.home), status.home_source


def test_the_data_root_is_the_argument_else_the_environment_else_the_last_choice_else_the_default(
    monkeypatch, tmp_path
):
    a, e, c = tmp_path / "argument", tmp_path / "environment", tmp_path / "chosen"
    assert home_source(monkeypatch, tmp_path, a, e, c) == (a, "argument")
    assert home_source(monkeypatch, tmp_path, None, e, c) == (e, "environment")
    assert home_source(monkeypatch, tmp_path, None, None, c) == (c, "config")
    assert home_source(monkeypatch, tmp_path) == (wequant_home(), "default")


def test_a_folder_without_complete_data_is_refused_and_nothing_switches(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ServiceError) as raised:
        service.data_use_home(service.UseHomeRequest(path=str(tmp_path / "empty")), Context(tmp_path / "home"))
    assert (raised.value.exit, raised.value.detail[0].code) == (Exit.USAGE, "not_a_data_root")
    assert user_config.configured_home() is None


def test_using_another_data_root_remembers_it_and_switches_the_server(tmp_path):
    other, switched = seed(tmp_path / "other").home, []
    context = Context(seed(tmp_path / "home").home, switch=switched.append)
    service.data_use_home(service.UseHomeRequest(path=str(other)), context)
    assert user_config.configured_home() == other and switched == [other]


def test_a_copy_is_used_once_checked_and_the_old_folder_stays(tmp_path):
    home, switched = seed(tmp_path / "home").home, []
    target = tmp_path / "new place"
    done = service.data_relocate(
        service.RelocateRequest(to=str(target), mode="copy"), Context(home, switch=switched.append)
    )
    assert (Path(done.home), switched, user_config.configured_home()) == (target, [target], target)
    assert Store(target).manifest() == Store(home).manifest()


def test_a_move_or_copy_waits_until_no_other_writer_holds_the_data_root(tmp_path):
    home = seed(tmp_path / "home").home
    with Store(home).lock():
        with pytest.raises(ServiceError) as raised:
            service.data_relocate(service.RelocateRequest(to=str(tmp_path / "new"), mode="move"), Context(home))
    assert (raised.value.exit, raised.value.detail[0].code) == (Exit.LOCKED, "store_busy")
    assert not (tmp_path / "new").exists() and Store(home).manifest() is not None


def test_the_setup_s_progress_is_kept_until_cleared(tmp_path):
    context = Context(tmp_path / "empty", clock=lambda: "2026-11-14T00:00:00+00:00")
    service.setup_save(service.SetupSaveRequest(step="folder", source="baostock", years=3), context)
    kept = service.setup_save(service.SetupSaveRequest(step="build", job_id=4), context).setup
    assert (kept.step, kept.source, kept.years, kept.job_id) == ("build", "baostock", 3, 4)
    assert service.setup_save(service.SetupSaveRequest(clear=True), context).setup == service.SetupState()


def test_folders_come_back_absolute_without_dots(tmp_path, monkeypatch):
    """F-32: a relative --home or path, with "..", reads as the folder it names."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x").mkdir()
    status = service.setup_status(service.SetupStatusRequest(), Context(Path("x/../home")))
    listing = service.fs_list(service.FolderRequest(path="x/.."), Context(Path("x/../home")))
    assert (status.home, listing.path) == (str(tmp_path / "home"), str(tmp_path))


def test_a_disk_without_room_says_how_much_the_data_needs(tmp_path, monkeypatch):
    """F-32: no_space's hint carries the free and the needed space."""
    usage = shutil._ntuple_diskusage(10**9, 10**9, 1)  # one byte free
    monkeypatch.setattr("wequant.storage.relocate.shutil.disk_usage", lambda path: usage)
    home = seed(tmp_path / "home").home
    with pytest.raises(ServiceError) as raised:
        service.data_relocate(service.RelocateRequest(to=str(tmp_path / "new"), mode="copy"), Context(home))
    detail = raised.value.detail[0]
    assert detail.code == "no_space" and detail.hint and detail.hint != ERROR_CODES["no_space"][1]
