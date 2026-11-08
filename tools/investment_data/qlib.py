"""Reader for Qlib's binary daily format: calendars/, instruments/ and features/."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_CODE = re.compile(r"(SH|SZ|BJ)([0-9]{6})\Z")


def to_wequant(code: str) -> str:
    """SH600000 -> 600000.SH."""
    match = _CODE.fullmatch(code)
    if match is None:
        raise ValueError(f"not a Qlib code: {code!r}")
    return f"{match.group(2)}.{match.group(1)}"


@dataclass(frozen=True)
class Span:
    code: str  # upstream form, e.g. SH600000
    start: np.datetime64
    end: np.datetime64


@dataclass
class Panel:
    """Every (instrument, day) inside the instrument spans, as flat arrays ordered by instrument, then day."""

    calendar: np.ndarray
    spans: list[Span]
    code_idx: np.ndarray
    cal_idx: np.ndarray
    values: dict[str, np.ndarray]
    issues: list[str]  # instruments skipped because their files do not match their span


def read_calendar(root: Path, freq: str = "day") -> np.ndarray:
    return np.array((root / "calendars" / f"{freq}.txt").read_text("utf-8").split(), dtype="datetime64[D]")


def read_instruments(path: Path) -> list[Span]:
    """One "code<TAB>start<TAB>end" line per span."""
    spans = []
    for line in path.read_text("utf-8").splitlines():
        code, start, end = line.split("\t")
        spans.append(Span(code, np.datetime64(start, "D"), np.datetime64(end, "D")))
    return spans


def read_feature(root: Path, code: str, field: str) -> tuple[int, np.ndarray]:
    """(calendar index of the first value, float32 values); the file's first float is that index."""
    raw = np.fromfile(root / "features" / code.lower() / f"{field}.day.bin", dtype="<f4")
    if raw.size == 0 or raw[0] < 0 or raw[0] != int(raw[0]):
        raise ValueError(f"{code}/{field}: no valid start index")
    return int(raw[0]), raw[1:]


def read_panel(root: Path, fields: tuple[str, ...]) -> Panel:
    calendar = read_calendar(root)
    spans = read_instruments(root / "instruments" / "all.txt")
    code_parts, cal_parts, issues = [], [], []
    value_parts: dict[str, list[np.ndarray]] = {f: [] for f in fields}
    for i, span in enumerate(spans):
        try:
            features = {f: read_feature(root, span.code, f) for f in fields}
        except (OSError, ValueError) as exc:
            issues.append(f"{span.code}: {exc}")
            continue
        layouts = {(start, values.size) for start, values in features.values()}
        if len(layouts) != 1:
            issues.append(f"{span.code}: fields differ in start or length {sorted(layouts)}")
            continue
        start, n = layouts.pop()
        end = start + n - 1
        if n == 0 or end >= calendar.size or (calendar[start], calendar[end]) != (span.start, span.end):
            issues.append(f"{span.code}: data does not cover exactly {span.start}..{span.end}")
            continue
        code_parts.append(np.full(n, i, np.int32))
        cal_parts.append(np.arange(start, end + 1, dtype=np.int32))
        for f in fields:
            value_parts[f].append(features[f][1])
    return Panel(
        calendar=calendar,
        spans=spans,
        code_idx=np.concatenate(code_parts),
        cal_idx=np.concatenate(cal_parts),
        values={f: np.concatenate(parts) for f, parts in value_parts.items()},
        issues=issues,
    )
