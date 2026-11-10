"""How a run reads anomalous data: flag its uses, or read the anomalous days of some levels as missing data."""

from __future__ import annotations

from dataclasses import dataclass

ANOMALY_MODES = ("flag", "exclude_day")


@dataclass(frozen=True)
class DataOptions:
    """flag uses every value and reports what it used (data_anomaly); exclude_day reads the days of error-level
    anomalies, and of suspicious ones too when exclude_suspicious, as missing data. Notices are never excluded.

    Excluding is a robustness check of whether results rest on disputed data, not a point-in-time rule: some anomaly
    labels rest on the next day's exchange preclose.
    """

    anomalies: str = "flag"  # one of ANOMALY_MODES
    exclude_suspicious: bool = False  # only with exclude_day

    def __post_init__(self) -> None:
        if not isinstance(self.anomalies, str) or self.anomalies not in ANOMALY_MODES:
            raise ValueError(f"anomalies {self.anomalies!r} is not one of {ANOMALY_MODES}")
        if not isinstance(self.exclude_suspicious, bool):
            raise ValueError(f"exclude_suspicious {self.exclude_suspicious!r} is not a bool")
        if self.exclude_suspicious and self.anomalies != "exclude_day":
            raise ValueError("exclude_suspicious applies to anomalies exclude_day only")

    def to_dict(self) -> dict[str, str | bool]:
        """Every field: the form a run archive and a spec store."""
        return {"anomalies": self.anomalies, "exclude_suspicious": self.exclude_suspicious}

    @property
    def excluded(self) -> tuple[str, ...]:
        """The anomaly levels a run's panel reads as missing."""
        if self.anomalies == "flag":
            return ()
        return ("error", "suspicious") if self.exclude_suspicious else ("error",)
