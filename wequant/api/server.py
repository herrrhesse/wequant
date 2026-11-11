"""Run the app under uvicorn in this process; its context (and so its caches) lives as long as the process."""

from __future__ import annotations

import uvicorn

from wequant.api.app import create_app
from wequant.service import Context


def run_server(context: Context, host: str, port: int) -> None:
    """Serve until Ctrl-C."""
    uvicorn.run(create_app(context), host=host, port=port, log_level="info")
