"""The wequant command."""

from __future__ import annotations

import sys

MISSING_EXTRA = 70  # Exit.INTERNAL, spelled out: wequant.service may be what cannot be imported


def main(argv: list[str] | None = None) -> None:
    """Run the wequant command; exit 70 with a hint when the cli extra is not installed."""
    try:
        from wequant.cli.commands import app  # imported here so a missing extra exits 70, not Python's 1
    except ModuleNotFoundError as error:
        if error.name not in ("cyclopts", "pydantic"):
            raise
        print(f"wequant: {error.name} is missing; install the cli extra: uv sync --extra cli", file=sys.stderr)
        raise SystemExit(MISSING_EXTRA) from None
    app(argv)
