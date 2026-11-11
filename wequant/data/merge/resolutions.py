"""The resolutions file: the user's decision on each merge conflict, one [[resolution]] table per conflict."""

from __future__ import annotations

import datetime as dt
import json
import re
import tomllib
from pathlib import Path

import polars as pl

from wequant.data import codes
from wequant.data.files import atomic_write
from wequant.data.merge.audit import OHLC

RESOLUTIONS = Path("resolutions") / "baostock.toml"  # under the data root, never in the repository (D20)
ENTRY_KEY = ("code", "date", "kind")
ENTRY = (*ENTRY_KEY, "fields", "take", "evidence", "status")
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
HEADER = """\
# Decisions on the BaoStock merge conflicts the merge cannot settle by itself.
#
# One [[resolution]] table per conflict, sorted by (date, code, kind); (code, date, kind) is unique.
#   code      "600000.SH"
#   date      2024-01-02
#   kind      "price_mismatch": BaoStock's open, high, low or close differ from the store's bar
#             "average_price": BaoStock's amount / volume lies outside [low, high] of the bar as its price entry
#             leaves it
#   fields    the bars fields the entry decides: ["open", "high", "low", "close"], ["amount"] or ["volume", "amount"]
#   take      "baostock", "seed" (the store's values) or "null" (amount only: unknown, the bar kept)
#   evidence  one sentence with the concrete numbers
#   status    "proposed" or "approved"; only the user approves
#
# The merge applies approved entries only, each recorded as one data_issues row. Any other conflict of these kinds
# stops the merge before anything is written. `python -m tools.baostock_merge propose` adds proposed entries.
"""  # the comment a new resolutions file starts with
_BLOCK = re.compile(r"(?m)^(?=\[\[resolution\]\])")


def default_path(home: Path) -> Path:
    """The data root's resolutions file."""
    return Path(home) / RESOLUTIONS


def load(path: Path) -> pl.DataFrame:
    """The file's entries in SCHEMA, none when it does not exist; ValueError names a malformed entry."""
    if not path.exists():
        return frame([])
    _, blocks = _split(path.read_text("utf-8"))
    entries = [entry for _, entry in blocks]
    keys = [_sort_key(e) for e in entries]
    if len(set(keys)) < len(keys):
        raise ValueError(f"resolutions repeat a (code, date, kind): {sorted(k for k in keys if keys.count(k) > 1)}")
    if keys != sorted(keys):
        raise ValueError("resolutions must be sorted by (date, code, kind)")
    return frame(entries)


def frame(entries: list[dict]) -> pl.DataFrame:
    """Entries in SCHEMA."""
    return pl.DataFrame([e | {"fields": list(e["fields"])} for e in entries], schema=SCHEMA)


def add(path: Path, proposals: list[dict]) -> int:
    """Insert the proposals whose (code, date, kind) the file lacks in sorted place; existing entries keep their text.
    A new file starts with HEADER. Returns how many were added."""
    header, blocks = _split(path.read_text("utf-8") if path.exists() else HEADER)
    present = {_sort_key(entry) for _, entry in blocks}
    new = [(render(p), _checked(p)) for p in proposals if _sort_key(p) not in present]
    ordered = sorted([*blocks, *new], key=lambda block: _sort_key(block[1]))
    body = "\n".join(block.rstrip() + "\n" for block, _ in ordered)
    atomic_write(path, (header.rstrip() + "\n" + ("\n" + body if body else "")).encode("utf-8"))
    return len(new)


def render(entry: dict) -> str:
    """One entry as a [[resolution]] table."""
    values = [f"{name} = {_toml(entry[name])}" for name in ENTRY]
    return "\n".join(["[[resolution]]", *values]) + "\n"


def _split(text: str) -> tuple[str, list[tuple[str, dict]]]:
    """(the comment header, (text, checked entry) of each entry); every entry starts at a line '[[resolution]]'."""
    header, *blocks = _BLOCK.split(text)
    if tomllib.loads(header):
        raise ValueError(
            "only comments may precede the first entry, and each entry starts with a line '[[resolution]]' at "
            f"column 0:\n{header}"
        )
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
