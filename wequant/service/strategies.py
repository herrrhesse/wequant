"""strategy validate | summary | schema | save | list | versions | show | archive: strategies and their saved versions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from pydantic import Field, JsonValue, model_validator
from pydantic_core import PydanticCustomError

from wequant.data.store import StoreLocked
from wequant.service.base import Context, Model, not_found
from wequant.service.details import Loc, spec_details, to_strategy, usage
from wequant.service.errors import Exit, ServiceError
from wequant.service.outputs import Params, SavedRef, Warning, warnings_out
from wequant.service.specs import ExecutionSpec, Name, StrategyRef, StrategySpec
from wequant.strategy import SpecError, SpecErrors, Strategy
from wequant.strategy.library import Entry, Library, NotSaved, read_warnings, strategy_hash
from wequant.strategy.section import weight_shares
from wequant.strategy.summary import sentence, summary

Hash = Annotated[str, Field(pattern="^[0-9a-f]{16}$")]  # first 16 hex digits of sha256 of the canonical form


class StrategyInput(Model):
    """A strategy given inline or as a saved version: exactly one of the two."""

    strategy: StrategySpec | None = None
    saved: StrategyRef | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> StrategyInput:
        if (self.strategy is None) == (self.saved is None):
            raise PydanticCustomError("requires", "give exactly one of strategy and saved")
        return self


class StrategyRequest(StrategyInput):
    """A strategy to check or summarise."""


class StrategyCheck(Model):
    """A valid strategy: its hash, canonical form and saved versions with the same content."""

    strategy_hash: Hash
    form: StrategySpec
    """The strategy as the core reads it, every default filled in; its canonical form leaves defaults out."""
    saved_as: list[SavedRef]
    """Saved versions with this content; empty: an unsaved variant."""
    weight_shares: dict[str, float]
    """Each enabled factor's share of the total weight, in factor order; empty: no enabled factor."""
    warnings: list[Warning]


class SummaryPart(Model):
    """One fragment of the summary sentence, pointing at the strategy fields it describes."""

    loc: list[list[str | int]]
    """The fields it describes, e.g. [["pool", "index"], ["universe"]]."""
    text: str
    """Chinese."""
    params: Params
    """Its numbers, e.g. {"top_n": 20} or each factor's weight share; empty: none."""


class StrategySummary(Model):
    """The Chinese summary sentence and its fragments."""

    strategy_hash: Hash
    text: str
    parts: list[SummaryPart]


class StrategySchemaRequest(Model):
    """No fields."""


class StrategySchema(Model):
    """JSON Schema 2020-12 documents of the strategy and of the execution block."""

    strategy: dict[str, JsonValue]
    execution: dict[str, JsonValue]


class SaveRequest(Model):
    """Save a strategy as a new version of a strategy id (or a new strategy)."""

    strategy: StrategySpec
    id: Name | None = None
    """null: a new strategy with a generated id."""
    name: Name | None = None
    """Display name; null: the id for a new strategy, unchanged for an existing one."""
    note: str | None = None


class SavedVersion(Model):
    """The version a save produced, or the current one when the content was unchanged."""

    id: str
    n: int
    strategy_hash: Hash
    created: bool
    """False: same content as the current version, no new version."""
    name: str
    saved_at: str
    """ISO 8601 with offset."""


class StrategyListRequest(Model):
    """List saved strategies."""

    archived: bool = False
    """Also list archived strategies."""


class StrategyInfo(Model):
    """One saved strategy."""

    id: str
    name: str
    created_at: str
    archived: bool
    versions: int
    current_n: int
    current_hash: Hash
    current_saved_at: str


class StrategyList(Model):
    """Saved strategies by id."""

    rows: list[StrategyInfo]


class VersionsRequest(Model):
    """The versions of one strategy."""

    id: Name


class VersionInfo(Model):
    """One saved version."""

    n: int
    strategy_hash: Hash
    saved_at: str
    strategy_version: int | None
    """STRATEGY_VERSION when saved; null: unknown (a version rebuilt from its file)."""
    note: str | None
    current: bool


class StrategyVersions(Model):
    """A strategy's versions, oldest first."""

    id: str
    name: str
    archived: bool
    rows: list[VersionInfo]


class ShowRequest(StrategyRef):
    """One saved version (n null: the current one)."""


class SavedStrategy(Model):
    """A saved version with its content and summary."""

    id: str
    n: int
    name: str
    strategy_hash: Hash
    saved_at: str
    strategy_version: int | None
    """null: unknown (rebuilt from its file)."""
    note: str | None
    strategy: StrategySpec
    summary: str
    warnings: list[Warning]
    """e.g. strategy_version_changed, meta_rebuilt."""


class ArchiveRequest(Model):
    """Archive a strategy (hidden from the list, files kept), or restore it."""

    id: Name
    restore: bool = False


def strategy_validate(request: StrategyRequest, context: Context = Context()) -> StrategyCheck:
    """Check a strategy: its hash, canonical form, weight shares and the saved versions with the same content."""
    library = context.library()
    strategy, warnings = resolve(request, library)
    found = strategy_hash(strategy)
    return StrategyCheck(
        strategy_hash=found,
        form=StrategySpec.model_validate(strategy.form),
        saved_as=saved_as(library, found),
        weight_shares=weight_shares(strategy),
        warnings=warnings,
    )


def strategy_summary(request: StrategyRequest, context: Context = Context()) -> StrategySummary:
    """The strategy in one Chinese sentence, with the fields each fragment describes."""
    strategy, _ = resolve(request, context.library())
    parts = summary(strategy)
    return StrategySummary(
        strategy_hash=strategy_hash(strategy),
        text=sentence(parts),
        parts=[SummaryPart(loc=[list(loc) for loc in p.loc], text=p.text, params=p.params) for p in parts],
    )


def strategy_schema(request: StrategySchemaRequest, context: Context = Context()) -> StrategySchema:
    """JSON Schemas of the strategy and of the execution block, for editors and for constraining generated strategies."""
    return StrategySchema(strategy=StrategySpec.model_json_schema(), execution=ExecutionSpec.model_json_schema())


def strategy_save(request: SaveRequest, context: Context = Context()) -> SavedVersion:
    """Save the strategy as the next version of id (a new strategy without one); unchanged content adds no version."""
    strategy = to_strategy(request.strategy)
    with located():
        entry, created = context.library().save(strategy, request.id, request.name, request.note)
    current = entry.current
    return SavedVersion(
        id=entry.id,
        n=current.n,
        strategy_hash=current.strategy_hash,
        created=created,
        name=entry.name,
        saved_at=current.saved_at,
    )


def strategy_list(request: StrategyListRequest, context: Context = Context()) -> StrategyList:
    """The saved strategies by id; archived ones only when asked for."""
    entries = context.library().entries()
    return StrategyList(rows=[_info(e) for e in entries if request.archived or not e.archived])


def strategy_versions(request: VersionsRequest, context: Context = Context()) -> StrategyVersions:
    """One strategy's versions, oldest first."""
    with located():
        entry = context.library().entry(request.id)
    rows = [VersionInfo(**vars(v), current=v is entry.current) for v in entry.versions]
    return StrategyVersions(id=entry.id, name=entry.name, archived=entry.archived, rows=rows)


def strategy_show(request: ShowRequest, context: Context = Context()) -> SavedStrategy:
    """One saved version (the current one without n) with its content and summary."""
    with located():
        entry, version, strategy = context.library().load(request.id, request.n)
    return SavedStrategy(
        id=entry.id,
        name=entry.name,
        **vars(version),
        strategy=StrategySpec.model_validate(strategy.form),
        summary=sentence(summary(strategy)),
        warnings=warnings_out(read_warnings(entry, version)),
    )


def strategy_archive(request: ArchiveRequest, context: Context = Context()) -> StrategyInfo:
    """Archive a strategy (hidden from the list, its files kept) or restore it."""
    with located():
        return _info(context.library().archive(request.id, request.restore))


def resolve(request: StrategyInput, library: Library) -> tuple[Strategy, list[Warning]]:
    """The core strategy of an inline or a saved strategy, with the warnings of reading a saved one."""
    if request.strategy is not None:
        return to_strategy(request.strategy), []
    with located(("saved",)):
        entry, version, strategy = library.load(request.saved.id, request.saved.n)
    return strategy, warnings_out(read_warnings(entry, version))


def saved_as(library: Library, found: str) -> list[SavedRef]:
    """The saved versions with this strategy hash."""
    return [SavedRef(id=id, n=n) for id, n in library.saved_as(found)]


@contextmanager
def located(loc: Loc = ()) -> Iterator[None]:
    """The library's errors as service errors located under loc: NOT_FOUND, USAGE or LOCKED."""
    try:
        yield
    except NotSaved as error:
        raise not_found(error.code, str(error), [*loc, "n" if error.code == "unknown_version" else "id"]) from None
    except (SpecError, SpecErrors) as error:
        raise usage(spec_details(error, loc)) from None
    except StoreLocked as error:
        raise ServiceError(Exit.LOCKED, str(error)) from None


def _info(entry: Entry) -> StrategyInfo:
    current = entry.current
    return StrategyInfo(
        id=entry.id,
        name=entry.name,
        created_at=entry.created_at,
        archived=entry.archived,
        versions=len(entry.versions),
        current_n=current.n,
        current_hash=current.strategy_hash,
        current_saved_at=current.saved_at,
    )
