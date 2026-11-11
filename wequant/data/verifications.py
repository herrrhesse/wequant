"""Verification records: what each source says about a cell, who checked it, how, and what is right (D20).

They live under the data root, never in the repository, and are only ever added to: each submission is one file written
once. A record's id is the digest of its content, so submitting the same record twice adds nothing. Resolutions and
data_issues cite records by id.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from wequant.canonical import digest
from wequant.data import codes
from wequant.data.files import atomic_write
from wequant.data.schema import LEVELS, TABLES
from wequant.data.store import exclusive
from wequant.strategy.errors import Collector, SpecError, SpecErrors

VERIFICATIONS = Path("verifications")
SOURCE_TYPES = ("exchange", "vendor", "announcement", "rule")
VERIFIERS = ("human", "agent")
UNITS = "prices and amounts in yuan, volume in shares, turn in percent"  # as the exchanges publish them
KEYS = ("object", "values", "sources", "verifier", "method", "conclusion", "correct", "level")
INTERNAL = ("baostock", "seed")  # value labels that need no source entry: the raw layer and the store


@dataclass(frozen=True)
class Verification:
    """One checked cell: KEYS as given, plus its id and when it was submitted."""

    id: str
    submitted_at: str
    record: dict  # KEYS, as JSON

    @property
    def table(self) -> str:
        return self.record["object"]["table"]

    @property
    def code(self) -> str:
        return self.record["object"]["code"]

    @property
    def date(self) -> dt.date:
        return dt.date.fromisoformat(self.record["object"]["date"])


def checked(record: Mapping) -> dict:
    """record with every key in place and its values in range; SpecErrors locates each fault."""
    found = Collector()
    with found.at():
        _require(record, KEYS)
    found.check()
    with found.at("object"):
        fields = _object(record["object"])
    sources = record["sources"] if isinstance(record["sources"], list) else []
    names = tuple(s["name"] for s in sources if isinstance(s, Mapping) and "name" in s)
    with found.at("sources"):
        _sources(record["sources"])
    with found.at("verifier"):
        _verifier(record["verifier"])
    with found.at("values"):
        _values(record["values"], fields, (*INTERNAL, *names))
    with found.at("correct"):
        _cells(record["correct"], fields)
    for key in ("method", "conclusion"):
        with found.at(key):
            _text(record[key])
    with found.at("level"):
        if record["level"] not in LEVELS:
            raise SpecError("not_a_choice", (), f"{record['level']!r} is not one of {LEVELS}")
    found.check()
    return json.loads(json.dumps({key: record[key] for key in KEYS}))


def record_id(record: Mapping) -> str:
    """'v-' and the digest of the checked record: equal content, equal id."""
    return "v-" + digest(dict(record))


class Verifications:
    """The records under root ($WEQUANT_HOME/verifications), one numbered file per submission."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def submit(self, records: Iterable[Mapping], now: dt.datetime | None = None) -> tuple[list[str], list[str]]:
        """(ids added, ids already there) of records, checked first; the new ones go into one new file."""
        found, new = [checked(r) for r in records], {}
        stamp = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive(self.root / ".lock"):
            known = {v.id for v in self.read()}
            for record in found:
                new.setdefault(record_id(record), record)
            added = {i: r for i, r in new.items() if i not in known}
            if added:
                lines = [
                    json.dumps({"id": i, "submitted_at": stamp, **r}, ensure_ascii=False) for i, r in added.items()
                ]
                name = (
                    f"{len(list(self.root.glob('*.jsonl'))) + 1:06d}-{stamp.replace(':', '')}.jsonl"  # sorts in order
                )
                atomic_write(self.root / name, ("\n".join(lines) + "\n").encode("utf-8"))
        ids = [record_id(r) for r in found]
        return [i for i in ids if i in added], [i for i in ids if i not in added]

    def read(self) -> list[Verification]:
        """Every record, by submission file then line."""
        found = []
        for path in sorted(self.root.glob("*.jsonl")) if self.root.is_dir() else []:
            for line in path.read_text("utf-8").splitlines():
                values = json.loads(line)
                found.append(Verification(values.pop("id"), values.pop("submitted_at"), values))
        return found


def _require(record: Mapping, keys: tuple[str, ...]) -> None:
    if not isinstance(record, Mapping):
        raise SpecError("wrong_type", (), "a record is an object")
    missing, extra = [k for k in keys if k not in record], [k for k in record if k not in keys]
    errors = [SpecError("missing_key", (k,), f"needs {k!r}") for k in missing]
    errors += [SpecError("unknown_key", (k,), f"no key {k!r}; keys: {keys}") for k in extra]
    if errors:
        raise errors[0] if len(errors) == 1 else SpecErrors(errors)


def _object(value: Mapping) -> tuple[str, ...]:
    _require(value, ("table", "code", "date", "fields"))
    if value["table"] not in TABLES:
        raise SpecError("not_a_choice", ("table",), f"{value['table']!r} is not a table")
    try:
        codes.split(value["code"])
    except (ValueError, AttributeError):
        raise SpecError("wrong_type", ("code",), f"{value['code']!r} is not a code such as 600000.SH") from None
    try:
        dt.date.fromisoformat(value["date"])
    except (ValueError, TypeError):
        raise SpecError("wrong_type", ("date",), f"{value['date']!r} is not a date YYYY-MM-DD") from None
    fields, columns = value["fields"], TABLES[value["table"]].schema
    if not isinstance(fields, list) or not fields or any(f not in columns for f in fields):
        raise SpecError("not_a_choice", ("fields",), f"{fields!r} is not a list of {value['table']} columns")
    return tuple(fields)


def _sources(value: list) -> None:
    if not isinstance(value, list) or not value:
        raise SpecError("out_of_range", (), "at least one source")
    found = Collector()
    for i, source in enumerate(value):
        with found.at(i):
            _source(source)
    found.check()
    names = [s["name"] for s in value]
    if len(set(names)) < len(names) or set(names) & set(INTERNAL):
        raise SpecError("duplicate_name", (), f"source names are unique and not {INTERNAL}: {names}")


def _source(value: Mapping) -> None:
    _require(value, ("name", "type", "url", "fetched_at"))
    if value["type"] not in SOURCE_TYPES:
        raise SpecError("not_a_choice", ("type",), f"{value['type']!r} is not one of {SOURCE_TYPES}")
    _text(value["name"])
    if value["url"] is not None and not isinstance(value["url"], str):
        raise SpecError("wrong_type", ("url",), "a URL is text or null")
    try:
        dt.datetime.fromisoformat(value["fetched_at"])
    except (ValueError, TypeError):
        raise SpecError("wrong_type", ("fetched_at",), f"{value['fetched_at']!r} is not an ISO time") from None


def _verifier(value: Mapping) -> None:
    _require(value, ("kind", "name", "model"))
    if value["kind"] not in VERIFIERS:
        raise SpecError("not_a_choice", ("kind",), f"{value['kind']!r} is not one of {VERIFIERS}")
    _text(value["name"])
    if value["model"] is not None and not isinstance(value["model"], str):
        raise SpecError("wrong_type", ("model",), "a model name is text or null")


def _values(value: Mapping, fields: tuple[str, ...], labels: tuple[str, ...]) -> None:
    if not isinstance(value, Mapping) or not value:
        raise SpecError("out_of_range", (), "the values of at least one source")
    for label, cells in value.items():
        if label not in labels:
            raise SpecError("bad_ref", (label,), f"{label!r} is neither {INTERNAL} nor a source's name")
        try:
            _cells(cells, fields)
        except SpecError as error:
            raise error.under(label) from None


def _cells(value: Mapping, fields: tuple[str, ...]) -> None:
    """field -> number, text or null, fields only."""
    if not isinstance(value, Mapping):
        raise SpecError("wrong_type", (), "an object of field values")
    for field, cell in value.items():
        if field not in fields:
            raise SpecError("unknown_key", (field,), f"{field!r} is not one of the object's fields {fields}")
        if not (cell is None or isinstance(cell, int | float | str)) or isinstance(cell, bool):
            raise SpecError("wrong_type", (field,), f"{cell!r} is not a number, text or null")


def _text(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SpecError("wrong_type", (), "non-empty text")
