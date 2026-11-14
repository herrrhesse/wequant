"""Peak resident memory for the reports: Linux's getrusage; NaN where the resource module does not exist (Windows), so
a report there says nan instead of failing to import."""

from __future__ import annotations

import math

try:
    import resource
except ImportError:  # Windows
    resource = None


def peak_mib(children: bool = False) -> float:
    """Peak RSS of this process (or of its finished children) in MiB; NaN where it cannot be read."""
    if resource is None:
        return math.nan
    who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    return resource.getrusage(who).ru_maxrss / 1024  # Linux reports KiB
