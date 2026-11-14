"""data use-home | move | copy, setup status | save (round 3, F-30): which folder is the data root, changing it, and
where the first-run setup has got. The chosen folder and the setup's progress are kept in the user's settings outside
any data root (wequant.user_config); a command's --home still comes first."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from wequant import user_config
from wequant.data.store import Store, StoreLocked
from wequant.data.update import Step
from wequant.paths import absolute
from wequant.service.base import Context, ErrorDetail, Model, manifest, not_found
from wequant.service.errors import Exit, ServiceError
from wequant.storage.relocate import (
    Cancelled,
    Refused,
    check_target,
    copy_root,
    files_of,
    is_data_root,
    problem,
    remove_source,
)

GIB = 1 << 30
STAGES = ("relocate_check", "relocate_copy", "relocate_switch", "relocate_remove")
MIB = 1 << 20


class UseHomeRequest(Model):
    """Read and write another, existing data root from now on."""

    path: str


class HomeSwitched(Model):
    """The data root now in use, and the one before."""

    home: str
    previous: str


class RelocateRequest(Model):
    """Move or copy the data root to a new or empty folder, then use it."""

    to: str
    mode: Literal["move", "copy"]
    """move: the old folder is removed once the copy is checked and in use; copy: it stays."""


class Relocated(Model):
    """A data root moved or copied and in use."""

    home: str
    previous: str
    mode: Literal["move", "copy"]
    files: int
    bytes: int
    renamed: bool
    """A move on one disk: the folder was renamed, nothing copied."""
    seconds: float
    left_behind: list[str]
    """After a move, files of the old folder that could not be removed (the data is safe in the new one)."""


class SetupState(Model):
    """How far the first-run setup has got, as the frontend saved it; every field may be null."""

    step: str | None = None
    """The wizard's step, e.g. folder, source, build, history, import_check, import, ready."""
    source: Literal["baostock", "import", "existing"] | None = None
    home: str | None = None
    """The folder chosen in the wizard."""
    years: Literal[1, 3, 5] | None = None
    """Years of history a BaoStock build starts with."""
    job_id: int | None = None
    """The build's or import's background job."""
    import_path: str | None = None
    """The folder of the user's own data to import."""
    updated_at: str | None = None


class SetupStatusRequest(Model):
    """No fields."""


class SetupStatus(Model):
    """The data root in use, whether it needs the first-run setup, and the setup's saved progress."""

    home: str
    home_source: Literal["argument", "environment", "config", "default"]
    """Where the folder comes from: --home, $WEQUANT_HOME, the user's last choice, or the system's default."""
    has_data: bool
    needs_setup: bool
    """True while the folder holds no WeQuant data: the frontend shows the first-run setup."""
    setup: SetupState


class SetupSaveRequest(SetupState):
    """The setup's progress to keep (each field given replaces the saved one); clear forgets all of it."""

    clear: bool = False


def data_use_home(request: UseHomeRequest, context: Context = Context()) -> HomeSwitched:
    """Use another folder that holds complete WeQuant data; refused, with the reason, for any other folder."""
    target = absolute(request.path)
    if (refused := problem(target)) is not None:
        raise _refusal(refused)
    current = context.store()
    with _held(current):
        _switch(context, target)
    return HomeSwitched(home=str(target), previous=str(current.home))


def data_relocate(request: RelocateRequest, context: Context = Context()) -> Relocated:
    """Copy the data root to a new or empty folder under the writer lock, check the copy, use it, and after a move
    remove the old folder; cancelled or failed, the old folder stays as it was and the new one is cleaned up."""
    store, started = context.store(), time.monotonic()
    manifest(store)  # NOT_FOUND no_store
    source, target = absolute(store.home), absolute(request.to)
    say = context.progress or (lambda step: None)
    say(Step(STAGES[0], 0.0, {}))
    files = files_of(source)
    size = sum((source / file).stat().st_size for file in files)
    with refusals():
        check_target(source, target, size)
    move = request.mode == "move"
    copied = _copied(store, source, target, move, say, context)
    left = []
    if move and not copied.renamed:
        say(Step(STAGES[3], 0.95, {}))
        left = [str(path) for path in remove_source(source, files)]
    seconds = round(time.monotonic() - started, 1)
    return Relocated(
        home=str(target),
        previous=str(source),
        mode=request.mode,
        files=copied.files,
        bytes=copied.bytes,
        renamed=copied.renamed,
        seconds=seconds,
        left_behind=left,
    )


def setup_status(request: SetupStatusRequest, context: Context = Context()) -> SetupStatus:
    """The data root in use and whether it needs the first-run setup; never fails on an empty or missing folder."""
    home = absolute(context.store().home)
    has_data = is_data_root(home)
    saved = SetupState.model_validate(user_config.load().get("setup") or {})
    return SetupStatus(
        home=str(home), home_source=_source(context), has_data=has_data, needs_setup=not has_data, setup=saved
    )


def setup_save(request: SetupSaveRequest, context: Context = Context()) -> SetupStatus:
    """Keep the setup's progress in the user's settings, so a closed browser can carry on; clear forgets it."""
    if request.clear:
        user_config.update(setup=None)
    else:
        given = request.model_dump(exclude={"clear"}, exclude_unset=True)
        saved = user_config.load().get("setup") or {}
        stamp = (context.clock or _now)()
        user_config.update(setup={**saved, **given, "updated_at": stamp})
    return setup_status(SetupStatusRequest(), context)


@contextmanager
def refusals() -> Iterator[None]:
    """A folder that cannot take the data as USAGE with its code; one that is not there as NOT_FOUND."""
    try:
        yield
    except Refused as error:
        raise _refusal(error) from None


def _copied(store: Store, source: Path, target: Path, move: bool, say, context: Context):
    """The copy, checked and switched to, under the writer lock (LOCKED store_busy while another writer holds it)."""
    progress = lambda done, total: say(  # noqa: E731
        Step(STAGES[1], 0.05 + 0.85 * done / max(total, 1), {"done_mb": done // MIB, "total_mb": total // MIB})
    )
    try:
        with store.lock(0):
            with refusals():
                copied = copy_root(source, target, move, progress, context.cancel or (lambda: False))
            say(Step(STAGES[2], 0.92, {}))
            _switch(context, target)
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error), [ErrorDetail(code="store_busy", message=str(error))]) from None
    except Cancelled as error:
        raise ServiceError(Exit.INTERRUPTED, str(error)) from None
    return copied


@contextmanager
def _held(store: Store) -> Iterator[None]:
    """The writer lock of a data root that has data (none to hold for an empty one), LOCKED store_busy if taken."""
    if not is_data_root(store.home):
        yield
        return
    try:
        with store.lock(0):
            yield
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error), [ErrorDetail(code="store_busy", message=str(error))]) from None


def _switch(context: Context, home: Path) -> None:
    """Remember home as the data root, and make the server read it from the next request on."""
    user_config.update(home=str(home))
    if context.switch is not None:
        context.switch(home)


def _refusal(error: Refused) -> ServiceError:
    if error.code == "no_directory":
        return not_found("no_directory", str(error), ["path"])
    detail = ErrorDetail(code=error.code, message=str(error), **_space(error))
    return ServiceError(Exit.USAGE, str(error), [detail])


def _space(error: Refused) -> dict:
    """no_space's hint with the sizes (F-32): how much the disk has free and how much the data takes."""
    if error.code != "no_space":
        return {}
    free, size = (error.params[key] / GIB for key in ("free", "size"))
    return {"hint": f"新位置所在的磁盘只剩 {free:.1f} GB，这些数据需要 {size:.1f} GB：请换一个位置，或者先腾出空间"}


def _source(context: Context) -> str:
    configured = user_config.configured_home()
    if context.home is not None:
        return "config" if configured is not None and Path(context.home) == configured else "argument"
    if os.environ.get("WEQUANT_HOME"):
        return "environment"
    return "config" if configured is not None else "default"


def _now() -> str:
    from wequant.data.files import utc_now

    return utc_now()
