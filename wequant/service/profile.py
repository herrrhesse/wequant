"""--profile: an operation's stage timings, written to stderr and never part of a result."""

from __future__ import annotations

import time

from wequant.service.base import Model


class Stage(Model):
    """One stage of an operation."""

    name: str
    """e.g. open_panel, compile, evaluate:<root>, cross_section, choose, day_loop, frames, metrics, archive_save."""
    seconds: float
    calls: int


class ProfileReport(Model):
    """An operation's total time and its stages in first-seen order."""

    operation: str
    seconds: float
    stages: list[Stage]


class Timer:
    """Stage timings of one call: mark(stage) ends a stage, timed from the previous mark (or the start)."""

    def __init__(self, operation: str) -> None:
        self.operation, self.started = operation, time.perf_counter()
        self.last, self.stages = self.started, {}

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        seconds, calls = self.stages.get(stage, (0.0, 0))
        self.stages[stage], self.last = (seconds + now - self.last, calls + 1), now

    def report(self) -> ProfileReport:
        stages = [Stage(name=name, seconds=s, calls=n) for name, (s, n) in self.stages.items()]
        return ProfileReport(operation=self.operation, seconds=self.last - self.started, stages=stages)
