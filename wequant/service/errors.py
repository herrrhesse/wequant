"""The exit codes of the wequant command and the one error an operation raises on purpose."""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    """Exit codes of the wequant command; README.md lists the same table."""

    OK = 0
    PROBLEMS = 1  # a check ran and found something to act on
    USAGE = 2  # bad arguments or input, a refused expression included
    NOT_FOUND = 3  # no store, store version, code or trading day
    UNREACHABLE = 4  # a data source cannot be reached
    LOCKED = 5  # another writer holds the store lock
    INTERNAL = 70  # a bug (sysexits EX_SOFTWARE)
    INTERRUPTED = 130  # Ctrl-C (128 + SIGINT)


class ServiceError(Exception):
    """A failure the caller can act on: exit is the code the command ends with, detail its located ErrorDetails,
    complete False when checks that relate fields to each other did not run."""

    def __init__(self, exit: Exit, message: str, detail: tuple = (), complete: bool = True) -> None:
        super().__init__(message)
        self.exit, self.detail, self.complete = exit, tuple(detail), complete
