"""The head of a check report: store version, code version, start time and every check with its measured number."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from wequant.version import code_version


@dataclass
class Summary:
    """The checks a report records as it computes them, for the table at its top; started is when it began."""

    started: dt.datetime = field(default_factory=lambda: dt.datetime.now().astimezone())
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, measured: str) -> str:
        """Record one check and what it measured; its verdict, PASS or FAIL."""
        verdict = "PASS" if ok else "FAIL"
        self.rows.append((name, verdict, measured))
        return verdict

    def head(self, title: str, version: int) -> list[str]:
        """The report's first lines: title, store version, code version, start time, then the summary table."""
        passed = sum(verdict == "PASS" for _, verdict, _ in self.rows)
        return [
            f"# {title}",
            "",
            f"- **Store version:** {version}",
            f"- **Code version:** {code_version()}",
            f"- **Started:** {self.started.isoformat(timespec='seconds')}",
            "",
            "## Summary",
            "",
            f"{passed} of {len(self.rows)} checks pass.",
            "",
            "| check | result | measured |",
            "| --- | --- | --- |",
            *(f"| {name} | {verdict} | {measured} |" for name, verdict, measured in self.rows),
            "",
        ]
