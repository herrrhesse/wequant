"""The resolutions file: the user's decision on each merge conflict, one [[resolution]] table per conflict.

It lives under the data root, never in the repository (D20); the code defines its format, reads and checks it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import polars as pl

from wequant.data import codes
from wequant.data.files import atomic_write
from wequant.data.merge.audit import OHLC
from wequant.data.schema import LEVELS

RESOLUTIONS = Path("resolutions") / "baostock.toml"  # under the data root, never in the repository (D20)
ENTRY_KEY = ("code", "date", "kind")
ENTRY = (*ENTRY_KEY, "fields", "take", "values", "category", "level", "verifications", "evidence", "status")
ENTRY += ("approval",)
TRADED = ("volume", "amount")
FIELDS = {  # kind: {take: the field lists such an entry decides}; corrected may set any non-empty part of CORRECTED
    "price_mismatch": {"baostock": (OHLC,), "seed": (OHLC,), "undetermined": (OHLC,)},
    "average_price": {"seed": (("amount",), TRADED), "null": (("amount",),), "undetermined": (("amount",), TRADED)},
    "amount_carried": {t: (("amount",),) for t in ("baostock", "seed", "null", "undetermined")},
    "stale_volume": {"no_bar": ((),), "baostock": (TRADED,), "undetermined": ((),)},
    "volume_truncated": {t: (TRADED,) for t in ("baostock", "seed", "undetermined")},
    "preclose_odd": {"baostock": (("preclose",),), "undetermined": (("preclose",),)},
}
CORRECTED = {  # kind: the fields a corrected entry may set, from verification records
    "price_mismatch": OHLC,
    "average_price": (*TRADED, "turn"),
    "amount_carried": ("amount",),
    "stale_volume": (*TRADED, "turn"),
    "volume_truncated": (*TRADED, "turn"),
    "preclose_odd": ("preclose",),
}
STATUSES = ("proposed", "approved", "rejected")
SCHEMA = {
    "code": pl.String,
    "date": pl.Date,
    "kind": pl.String,
    "fields": pl.List(pl.String),
    "take": pl.String,
    "values": pl.String,  # JSON object, field -> value in store units
    "category": pl.String,
    "level": pl.String,
    "verifications": pl.List(pl.String),
    "evidence": pl.String,
    "status": pl.String,
    "approval": pl.String,
}
HEADER = """\
# Decisions on the BaoStock merge conflicts the merge cannot settle by itself.
#
# One [[resolution]] table per conflict, sorted by (date, code, kind); (code, date, kind) is unique.
#   code           "600000.SH"
#   date           2024-01-02
#   kind           price_mismatch: BaoStock's open, high, low or close differ from the store's bar
#                  average_price: BaoStock's amount / volume lies outside [low, high] of the bar
#                  amount_carried: BaoStock's amount repeats the code's previous row while the volume differs
#                  stale_volume: a suspended row (tradestatus 0, amount 0) still carries a volume
#                  volume_truncated: on a day whose snapshot was cut short, BaoStock's volume is below the store's
#                  preclose_odd: BaoStock's exchange preclose is not a whole fen
#   fields         the bars fields the entry decides
#   take           baostock | seed (the store's values) | null (amount unknown, bar kept) | no_bar (none built)
#                  | corrected (values from verification records) | undetermined (never approved: needs evidence)
#   values         corrected only: field = value in store units (prices in 厘, volume in shares, amount in yuan)
#   category       the group it belongs to, e.g. "D11-B"
#   level          error (contradicted the exchange, corrected) | suspicious (unverified) | notice (noted only)
#   verifications  ids of the verification records it rests on (wequant data verify list --id ...)
#   evidence       one sentence with the concrete numbers
#   status         proposed | approved | rejected; only the user decides (wequant data resolve)
#   approval       who decided and on what basis; empty until decided
#
# The merge applies approved entries only, each recorded as one data_issues row citing its verifications. Any other
# conflict of these kinds stops the merge before anything is written.
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
    rows = [e | {"fields": list(e["fields"]), "values": json.dumps(e["values"], sort_keys=True)} for e in entries]
    return pl.DataFrame(rows, schema=SCHEMA)


def add(path: Path, proposals: list[dict]) -> int:
    """Insert the proposals whose (code, date, kind) the file lacks in sorted place; existing entries keep their text.
    A new file starts with HEADER. Returns how many were added."""
    header, blocks = _split(path.read_text("utf-8") if path.exists() else HEADER)
    present = {_sort_key(entry) for _, entry in blocks}
    new = [(render(p), _checked(p)) for p in proposals if _sort_key(p) not in present]
    _write(path, header, [*blocks, *new])
    return len(new)


def decide(path: Path, chosen: Callable[[dict], bool], status: str, approval: str) -> list[dict]:
    """Set status and approval on the chosen entries, rewriting only theirs; the entries changed. ValueError names an
    undetermined entry a decision would approve; nothing is written then."""
    header, blocks = _split(path.read_text("utf-8"))
    picked = [entry for _, entry in blocks if chosen(entry)]
    blocked = [e for e in picked if status == "approved" and e["take"] == "undetermined"]
    if blocked:
        listed = ", ".join(f"{e['code']} {e['date']} {e['kind']}" for e in blocked[:20])
        raise ValueError(f"{len(blocked)} entries take 'undetermined' and need evidence first: {listed}")
    changed = []
    for i, (text, entry) in enumerate(blocks):
        if chosen(entry) and (entry["status"], entry["approval"]) != (status, approval):
            entry = _checked(entry | {"status": status, "approval": approval})
            blocks[i] = (render(entry), entry)
            changed.append(entry)
    if changed:
        _write(path, header, blocks)
    return changed


def render(entry: dict) -> str:
    """One entry as a [[resolution]] table."""
    values = [f"{name} = {_toml(entry[name])}" for name in ENTRY]
    return "\n".join(["[[resolution]]", *values]) + "\n"


def _write(path: Path, header: str, blocks: list[tuple[str, dict]]) -> None:
    ordered = sorted(blocks, key=lambda block: _sort_key(block[1]))
    body = "\n".join(block.rstrip() + "\n" for block, _ in ordered)
    atomic_write(path, (header.rstrip() + "\n" + ("\n" + body if body else "")).encode("utf-8"))


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
    """entry with fields and verifications as tuples, once its keys and values follow the format."""
    if set(entry) != set(ENTRY):
        raise ValueError(f"a resolution has exactly the keys {ENTRY}: {entry}")
    codes.split(entry["code"])
    fields, kind, take, values = tuple(entry["fields"]), entry["kind"], entry["take"], entry["values"]
    problems = {
        "date": not isinstance(entry["date"], dt.date) or isinstance(entry["date"], dt.datetime),
        "kind": kind not in FIELDS,
        "fields for its kind and take": kind in FIELDS and not _allowed(kind, take, fields),
        "values only for corrected, one per field": set(values) != (set(fields) if take == "corrected" else set()),
        "verifications for corrected": take == "corrected" and not entry["verifications"],
        "verifications are ids v-...": any(not str(v).startswith("v-") for v in entry["verifications"]),
        "level": entry["level"] not in LEVELS,
        "status": entry["status"] not in STATUSES,
        "undetermined never approved": take == "undetermined" and entry["status"] == "approved",
        "category and evidence are text": not all(_text(entry[k]) for k in ("category", "evidence")),
        "approval is text": not isinstance(entry["approval"], str),
    }
    wrong = [name for name, broken in problems.items() if broken]
    if wrong:
        raise ValueError(f"malformed resolution ({', '.join(wrong)}): {entry}")
    entry = {name: entry[name] for name in ENTRY}
    return entry | {"fields": fields, "verifications": tuple(entry["verifications"]), "values": dict(values)}


def _allowed(kind: str, take: str, fields: tuple) -> bool:
    if take == "corrected":
        return bool(fields) and set(fields) <= set(CORRECTED[kind])
    return fields in FIELDS[kind].get(take, ())


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sort_key(entry: dict) -> tuple:
    return entry["date"], entry["code"], entry["kind"]


def _toml(value) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k} = {_toml(v)}" for k, v in sorted(value.items())) + "}"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid TOML basic string
