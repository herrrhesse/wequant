"""Canonical JSON: the one text that keys, run ids and strategy hashes hash, and the bytes of JSON files kept."""

from __future__ import annotations

import hashlib
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


def digest(value: object) -> str:
    """First 16 hex digits of the sha256 of canonical(value)."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:16]


def document(value: object) -> bytes:
    """JSON bytes that depend only on the content: sorted keys, indent 1, no NaN, a final newline."""
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False).encode() + b"\n"
