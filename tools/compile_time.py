"""The first start on this machine (round 3, W0): seconds a fresh interpreter takes to import wequant.ops and make its
first MA, CORR and CS_RANK calls with an empty numba cache (every kernel compiles), then again with the cache that run
wrote. The Windows CI job prints it.

    uv run python -m tools.compile_time
"""

from __future__ import annotations

import tempfile

from tools.ops_report import probe


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wequant-numba-") as empty:
        cold, warm = probe(empty), probe(empty)
    for label, (imported, *calls) in (("empty cache", cold), ("warm cache", warm)):
        print(f"{label}: import {imported:.2f} s, first calls {sum(calls):.2f} s, total {imported + sum(calls):.2f} s")


if __name__ == "__main__":
    main()
