"""Reading a pasted list or an uploaded CSV/TXT file of stocks (round 3): each item is a code in a common form or a
stock's current name, resolved against the data's stocks; an item that resolves to nothing says why."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import NamedTuple

from wequant.data.codes import security_type

SEPARATORS = re.compile(r"[\s,，;；、|]+")
CODE = re.compile(r"(?:(SH|SZ|BJ)\.?)?([0-9]{6})(?:\.(SH|SZ|BJ))?", re.IGNORECASE)  # 600519, sh.600519, 600519.SH
HEADERS = frozenset(
    ("代码", "名称", "股票代码", "股票名称", "证券代码", "证券简称", "证券名称", "简称", "code", "name", "symbol")
)
REASONS = ("not_found", "ambiguous", "beijing", "not_a_stock", "header")


class Unparsed(NamedTuple):
    """An item that names no one stock: why (REASONS), and the stocks it could mean when ambiguous."""

    item: str
    reason: str
    candidates: tuple[str, ...] = ()


class Parsed(NamedTuple):
    """The stocks found, in the order first named; the items that named none; the items naming one already found."""

    codes: list[str]
    unparsed: list[Unparsed]
    duplicates: list[str]


def parse(text: str, stocks: Mapping[str, str | None]) -> Parsed:
    """The items of text, split at line breaks, commas, spaces, tabs and semicolons; stocks maps each stock code of
    the data to its current name."""
    names = defaultdict(list)
    for code, name in stocks.items():
        if name:
            names[name].append(code)
    found, unparsed, duplicates = {}, [], []
    for item in filter(None, SEPARATORS.split(text.replace("﻿", ""))):
        code, problem = _resolve(item, stocks, names)
        if problem is not None:
            unparsed.append(problem)
        elif code in found:
            duplicates.append(item)
        else:
            found[code] = None
    return Parsed(list(found), unparsed, duplicates)


def _resolve(item: str, stocks: Mapping, names: Mapping) -> tuple[str | None, Unparsed | None]:
    if item.lower() in HEADERS:
        return None, Unparsed(item, "header")
    if match := CODE.fullmatch(item):
        return _code(item, match, stocks)
    named = sorted(names.get(item, ()))
    if len(named) == 1:
        return named[0], None
    return None, Unparsed(item, "ambiguous", tuple(named)) if named else Unparsed(item, "not_found")


def _code(item: str, match: re.Match, stocks: Mapping) -> tuple[str | None, Unparsed | None]:
    """A code-shaped item: its exchange from the prefix or suffix, else whichever of SH and SZ the data has."""
    prefix, digits, suffix = match.groups()
    exchanges = {e.upper() for e in (prefix, suffix) if e}
    candidates = [f"{digits}.{e}" for e in (exchanges or ("SH", "SZ"))]
    known = [code for code in candidates if code in stocks]
    if len(known) == 1 and len(exchanges) < 2:
        return known[0], None
    if len(known) > 1:
        return None, Unparsed(item, "ambiguous", tuple(known))
    if "BJ" in exchanges or (not exchanges and (digits[0] in "48" or digits[:2] == "92")):
        return None, Unparsed(item, "beijing")
    kinds = {_kind(code) for code in candidates}
    if kinds & {"index", "fund"} and "stock" not in kinds:
        return None, Unparsed(item, "not_a_stock")
    return None, Unparsed(item, "not_found")


def _kind(code: str) -> str | None:
    try:
        return security_type(code)
    except ValueError:
        return None
