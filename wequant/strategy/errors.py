"""Errors of a description: a stable code, its location in the form and, inside an expression, the path to the fault."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

Loc = tuple[str | int, ...]


class SpecError(ValueError):
    """A description that breaks one rule; loc names the field like pydantic, path the node inside its expression."""

    def __init__(self, code: str, loc: Loc, message: str, path: tuple[int, ...] = ()) -> None:
        super().__init__(f"{'.'.join(map(str, loc))}: {message}" if loc else message)
        self.code, self.loc, self.message, self.path = code, tuple(loc), message, tuple(path)

    @property
    def errors(self) -> tuple[SpecError, ...]:
        """(self,), so one error reads like SpecErrors."""
        return (self,)

    def under(self, *prefix: str | int) -> SpecError:
        """The same error located under prefix."""
        return SpecError(self.code, (*prefix, *self.loc), self.message, self.path)


class SpecErrors(ValueError):
    """Every field error of one form, in the form's order."""

    def __init__(self, errors: Iterable[SpecError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(map(str, self.errors)))


class Collector:
    """Errors gathered field by field, raised together by check()."""

    def __init__(self) -> None:
        self.errors: list[SpecError] = []

    @contextmanager
    def at(self, *loc: str | int) -> Iterator[None]:
        """Run the block, keeping a SpecError or SpecErrors it raises, located under loc."""
        try:
            yield
        except (SpecError, SpecErrors) as error:
            self.errors += [e.under(*loc) for e in error.errors]

    def check(self) -> None:
        """SpecErrors of everything kept, if anything was."""
        if self.errors:
            raise SpecErrors(self.errors)
