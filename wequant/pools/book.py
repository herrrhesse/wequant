"""Stock pools (round 3): the user's named lists of stocks, one file each, <home>/pools/<id>.json."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wequant.canonical import document
from wequant.data.codes import is_code
from wequant.data.files import atomic_write, check_inside, read_text, utc_now
from wequant.strategy.errors import SpecError

FORMAT = 1  # bump when a pool file changes meaning
POOLS = "pools"


class NoPool(LookupError):
    """No stock pool with this id."""


@dataclass(frozen=True)
class StockPool:
    """A named list of stocks, in the user's order, without repeats."""

    id: str
    name: str
    codes: tuple[str, ...]
    created_at: str
    modified_at: str


class PoolBook:
    """The stock pools of a data root."""

    def __init__(self, home: Path, clock: Callable[[], str] | None = None) -> None:
        """clock stamps what save writes (tests, the demo home)."""
        self.home, self.root, self.clock = Path(home), Path(home) / POOLS, clock or utc_now

    def pools(self) -> list[StockPool]:
        """Every pool, oldest first."""
        found = [_read(path) for path in self.root.glob("p-*.json")] if self.root.is_dir() else []
        return sorted(found, key=lambda pool: (pool.created_at, pool.id))

    def load(self, id: str) -> StockPool:
        """The pool id; NoPool without one."""
        path = self._path(id)
        if not path.is_file():
            raise NoPool(f"no stock pool {id!r}")
        return _read(path)

    def save(self, name: str, codes: Sequence[str], id: str | None = None) -> StockPool:
        """Pool id renamed and refilled (a new pool without id); SpecError for an empty name or a bad or repeated
        code, NoPool for an unknown id."""
        _check(name, codes)
        now = self.clock()
        created = self.load(id).created_at if id is not None else now
        pool = StockPool(id or self._new_id(), name, tuple(codes), created, now)
        check_inside(self.home, [self.root])
        atomic_write(self._path(pool.id), document({"format": FORMAT, **vars(pool), "codes": list(pool.codes)}))
        return pool

    def delete(self, id: str) -> StockPool:
        """Remove pool id; the strategies that copied its stocks keep their copies."""
        pool = self.load(id)
        check_inside(self.home, [self.root])
        self._path(id).unlink()
        return pool

    def _path(self, id: str) -> Path:
        return self.root / f"{id}.json"

    def _new_id(self) -> str:
        while self._path(id := f"p-{secrets.token_hex(4)}").exists():
            pass
        return id


def _check(name: str, codes: Sequence[str]) -> None:
    if not isinstance(name, str) or not name.strip():
        raise SpecError("out_of_range", ("name",), "a pool's name is not empty")
    seen = set()
    for i, code in enumerate(codes):
        if not isinstance(code, str) or not is_code(code):
            raise SpecError("wrong_type", ("codes", i), f"{code!r} is not a code such as 600000.SH")
        if code in seen:
            raise SpecError("duplicate_value", ("codes", i), f"{code!r} is given twice")
        seen.add(code)


def _read(path: Path) -> StockPool:
    data = json.loads(read_text(path))
    fields = ("id", "name", "created_at", "modified_at")
    return StockPool(**{key: data[key] for key in fields}, codes=tuple(data["codes"]))
