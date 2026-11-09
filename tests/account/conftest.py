"""Hypothesis profiles for the account tests; HYPOTHESIS_PROFILE=deep runs 2000 examples per test."""

import os

from hypothesis import settings

settings.register_profile("default", deadline=None, max_examples=100, derandomize=True, database=None)
settings.register_profile("deep", settings.get_profile("default"), max_examples=2000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
