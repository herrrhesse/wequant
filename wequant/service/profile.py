"""--profile: an operation's stage timings, written to stderr and never part of a result."""

from __future__ import annotations

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
