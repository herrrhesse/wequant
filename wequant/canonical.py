"""Canonical JSON: the one text that keys and run ids hash."""

from __future__ import annotations

import json


def canonical(value: object) -> str:
    """Compact JSON with sorted keys; ValueError unless value is plain, finite JSON."""
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"not plain JSON: {error}") from None
    if json.loads(text) != value:  # a tuple or a non-str key comes back changed
        raise ValueError(f"not plain JSON: {value!r}")
    return text
