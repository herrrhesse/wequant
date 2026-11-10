"""strategy validate | summary | schema | save | list | versions | show | archive: strategies and their saved versions."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from wequant.service.base import Model
from wequant.service.outputs import Params, SavedRef, Warning
from wequant.service.specs import Name, StrategyRef, StrategySpec

Hash = Annotated[str, Field(pattern="^[0-9a-f]{16}$")]  # first 16 hex digits of sha256 of the canonical form


class StrategyInput(Model):
    """A strategy given inline or as a saved version: exactly one of the two."""

    strategy: StrategySpec | None = None
    saved: StrategyRef | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> StrategyInput:
        if (self.strategy is None) == (self.saved is None):
            raise ValueError("give exactly one of strategy and saved")
        return self


class StrategyRequest(StrategyInput):
    """A strategy to check or summarise."""


class StrategyCheck(Model):
    """A valid strategy: its hash, canonical form and saved versions with the same content."""

    strategy_hash: Hash
    form: StrategySpec
    """The canonical form, defaults left out."""
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
