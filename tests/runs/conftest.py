"""Hypothesis profiles and the archive's clock for the run tests; HYPOTHESIS_PROFILE=deep runs 2000 examples per test."""

import os

import pytest
from hypothesis import settings

from wequant.runs import record

settings.register_profile("default", deadline=None, max_examples=100, derandomize=True, database=None)
settings.register_profile("deep", settings.get_profile("default"), max_examples=2000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def clock(monkeypatch):
    """clock(text) makes the archive's now() return text."""

    def set_now(text: str) -> None:
        monkeypatch.setattr(record, "now", lambda: text)

    return set_now
