"""One finished backtest, as every engine returns it and the archive keeps it."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from wequant.account import Ledger
from wequant.analytics import Metric, MetricSpec
from wequant.canonical import canonical

FORMAT = 2  # bump when the run id, a Run field or an archived file changes meaning
_CODE = re.compile(r"[a-z][a-z0-9_]*")


def run_inputs(
    engine: str,
    description: Mapping,
    versions: Mapping,
    *,
    store_version: int,
    data_key: str,
    rules_key: str,
    panel_key: str,
) -> dict:
    """What decides a run's results, known before it runs: equal inputs give the same id and the same archived bytes."""
    return {
        "format": FORMAT,
        "engine": engine,
        "description": dict(description),
        "store_version": store_version,
        "data_key": data_key,
        "rules_key": rules_key,
        "panel_key": panel_key,
        "versions": dict(versions),
    }


def run_id(inputs: Mapping) -> str:
    """First 16 hex of the sha256 of canonical(inputs)."""
    return hashlib.sha256(canonical(dict(inputs)).encode()).hexdigest()[:16]


@dataclass(frozen=True, eq=False)
class Run:
    """One finished backtest: what decided it, the panel its signals read and everything it produced."""

    engine: str  # the engine's name, e.g. "rebalance"
    description: dict  # complete canonical description, plain JSON: the engine runs it again from this alone
    versions: dict[str, int]  # meaning versions of the engine and of each module it used
    panel_key: str  # wequant.expr.panel_key of the panel the expressions ran on
    anchor: dt.date  # that panel's first day
    ledger: Ledger  # the account's frames and identities; daily may carry a benchmark column
    metrics: dict[str, Metric]  # compute_metrics of the ledger's frames
    spec: MetricSpec  # the conventions the metrics were computed with
    picks: pl.DataFrame  # each decision day's picks: the engine's fixed schema and row order
    funnel: pl.DataFrame  # each decision day's funnel counts: the engine's fixed schema and row order
    detail: pl.DataFrame  # each decision day's per-stock selection: the engine's columns and row order
    warnings: dict[str, str]  # snake_case code -> message, in the engine's order

    def __post_init__(self) -> None:
        """ValueError unless description and versions are plain JSON objects and warnings map codes to text."""
        for name in ("description", "versions"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} is a {type(value).__name__}, not a mapping")
            object.__setattr__(self, name, json.loads(canonical(dict(value))))
        if not all(type(v) is int for v in self.versions.values()):
            raise ValueError(f"versions must be ints: {self.versions}")
        if not all(isinstance(k, str) and _CODE.fullmatch(k) and isinstance(v, str) for k, v in self.warnings.items()):
            raise ValueError(f"warnings must map snake_case codes to text: {self.warnings}")

    @property
    def inputs(self) -> dict:
        """run_inputs of this run, from its ledger's identities and its panel_key."""
        meta = self.ledger.meta
        return run_inputs(
            self.engine,
            self.description,
            self.versions,
            store_version=meta["store_version"],
            data_key=meta["data_key"],
            rules_key=meta["rules_key"],
            panel_key=self.panel_key,
        )

    @property
    def id(self) -> str:
        """run_id of the inputs: known as soon as the panel is."""
        return run_id(self.inputs)

    @property
    def store_version(self) -> int:
        """The store version the run read."""
        return self.ledger.meta["store_version"]

    @property
    def code_version(self) -> str:
        """The code that produced the run."""
        return self.ledger.meta["code_version"]
