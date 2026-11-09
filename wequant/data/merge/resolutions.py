"""The resolutions file: the user's decision on each merge conflict, one [[resolution]] table per conflict."""

from __future__ import annotations

import datetime as dt
import json
import re
import tomllib
from pathlib import Path

import polars as pl

from wequant.data import codes

PACKAGED = Path(__file__).with_name("resolutions.toml")
KEY = ("code", "date", "kind")
ENTRY = (*KEY, "fields", "take", "evidence", "status")
OHLC = ("open", "high", "low", "close")
FIELDS = {  # (kind, take): the field lists such an entry may decide
    ("price_mismatch", "baostock"): (OHLC,),
    ("price_mismatch", "seed"): (OHLC,),
    ("average_price", "seed"): (("amount",), ("volume", "amount")),
    ("average_price", "null"): (("amount",),),
}
STATUSES = ("proposed", "approved")
SCHEMA = {
    "code": pl.String,
    "date": pl.Date,
    "kind": pl.String,
    "fields": pl.List(pl.String),
    "take": pl.String,
    "evidence": pl.String,
    "status": pl.String,
}
_BLOCK = re.compile(r"(?m)^(?=\[\[resolution\]\])")


def load(path: Path | None = None) -> pl.DataFrame:
    """The file's entries (the packaged file by default) in SCHEMA; ValueError names a malformed entry."""
    _, blocks = _split((path or PACKAGED).read_text("utf-8"))
    entries = [entry for _, entry in blocks]
    keys = [_sort_key(e) for e in entries]
    if len(set(keys)) < len(keys):
        raise ValueError(f"resolutions repeat a (code, date, kind): {sorted(k for k in keys if keys.count(k) > 1)}")
    if keys != sorted(keys):
        raise ValueError("resolutions must be sorted by (date, code, kind)")
    return pl.DataFrame([e | {"fields": list(e["fields"])} for e in entries], schema=SCHEMA)


def add(path: Path, proposals: list[dict]) -> int:
    """Insert the proposals whose (code, date, kind) the file lacks in sorted place; existing entries keep their text.
    A new file starts with the packaged file's header. Returns how many were added."""
    header, blocks = _split(path.read_text("utf-8") if path.exists() else _split(PACKAGED.read_text("utf-8"))[0])
    present = {_sort_key(entry) for _, entry in blocks}
    new = [(render(p), _checked(p)) for p in proposals if _sort_key(p) not in present]
    ordered = sorted([*blocks, *new], key=lambda block: _sort_key(block[1]))
    body = "\n".join(text.rstrip() + "\n" for text, _ in ordered)
    path.write_text(header.rstrip() + "\n" + ("\n" + body if body else ""), "utf-8")
    return len(new)


def render(entry: dict) -> str:
    """One entry as a [[resolution]] table."""
    values = [f"{name} = {_toml(entry[name])}" for name in ENTRY]
    return "\n".join(["[[resolution]]", *values]) + "\n"


def _split(text: str) -> tuple[str, list[tuple[str, dict]]]:
    """(the text before the first entry, (text, checked entry) of each entry)."""
    header, *blocks = _BLOCK.split(text)
    return header, [(block, _parsed(block)) for block in blocks]


def _parsed(block: str) -> dict:
    document = tomllib.loads(block)
    if set(document) != {"resolution"} or len(document["resolution"]) != 1:
        raise ValueError(f"expected one [[resolution]] table:\n{block}")
    return _checked(document["resolution"][0])


def _checked(entry: dict) -> dict:
    """entry with fields as a tuple, once its keys and values follow the format."""
    if set(entry) != set(ENTRY):
        raise ValueError(f"a resolution has exactly the keys {ENTRY}: {entry}")
    codes.split(entry["code"])
    fields = tuple(entry["fields"])
    problems = [
        not isinstance(entry["date"], dt.date) or isinstance(entry["date"], dt.datetime),
        fields not in FIELDS.get((entry["kind"], entry["take"]), ()),
        entry["status"] not in STATUSES,
        not isinstance(entry["evidence"], str) or not entry["evidence"].strip(),
    ]
    if any(problems):
        raise ValueError(f"malformed resolution {entry}; allowed (kind, take): fields {FIELDS}, status {STATUSES}")
    return {name: fields if name == "fields" else entry[name] for name in ENTRY}


def _sort_key(entry: dict) -> tuple:
    return entry["date"], entry["code"], entry["kind"]


def _toml(value) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string
