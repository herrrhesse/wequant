"""Run the app under uvicorn in this process; its context (and so its caches) lives as long as the process."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from wequant.api.app import create_app
from wequant.service import Context


def run_server(context: Context, host: str, port: int, static: Path | None = None) -> None:
    """Serve until Ctrl-C, the frontend build from static (default: static_dir())."""
    uvicorn.run(create_app(context, static=static), host=host, port=port, log_level="info")
