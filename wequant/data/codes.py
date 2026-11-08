"""Security codes (600000.SH / 000001.SZ / 920047.BJ): parsing, type and board.

The exchange always comes from the source's prefix or suffix, never from the digits:
000001.SH is an index, 000001.SZ a stock.
"""

from __future__ import annotations

import re

EXCHANGES = ("SH", "SZ", "BJ")
BOARDS = ("main", "chinext", "star", "bse")
SECURITY_TYPES = ("stock", "index", "fund")

_WEQUANT_RE = re.compile(r"([0-9]{6})\.(SH|SZ|BJ)\Z")


def split(code: str) -> tuple[str, str]:
    """600000.SH -> ("600000", "SH")."""
    match = _WEQUANT_RE.fullmatch(code)
    if match is None:
        raise ValueError(f"not a wequant code: {code!r}")
    return match.group(1), match.group(2)


def security_type(code: str) -> str:
    """stock / index / fund for a wequant code; ValueError for anything unrecognized.

    index: SH 000xxx, SZ 399xxx, BJ 899xxx.
    fund:  SH 5xxxxx, SZ 15xxxx / 16xxxx / 18xxxx (ETFs, LOFs, closed-end funds).
    stock: codes that board() recognizes.
    """
    digits, exchange = split(code)
    if (exchange, digits[:3]) in (("SH", "000"), ("SZ", "399"), ("BJ", "899")):
        return "index"
    if (exchange == "SH" and digits[0] == "5") or (exchange == "SZ" and digits[:2] in ("15", "16", "18")):
        return "fund"
    board(code)
    return "stock"


def board(code: str) -> str:
    """Listing board of a stock, from its code prefix.

    main:    SH 600/601/603/605, SZ 000/001/002/003 (002 was the SME board until 2021)
    chinext: SZ 300/301/302
    star:    SH 688/689
    bse:     BJ 4xxxxx/8xxxxx (codes before the 2025 migration) and 920xxx
    """
    digits, exchange = split(code)
    prefix = digits[:3]
    if exchange == "SH":
        if prefix in ("600", "601", "603", "605"):
            return "main"
        if prefix in ("688", "689"):
            return "star"
    elif exchange == "SZ":
        if prefix in ("000", "001", "002", "003"):
            return "main"
        if prefix in ("300", "301", "302"):
            return "chinext"
    elif exchange == "BJ":
        if digits[0] in ("4", "8") or prefix == "920":
            return "bse"
    raise ValueError(f"no board rule for {code}")


def bse_code_era(code: str) -> str:
    """old (4xxxxx/8xxxxx) or new (920xxx) for a Beijing Stock Exchange stock code.

    The two are not merged here; a later code-mapping table will link them.
    """
    digits, exchange = split(code)
    if exchange != "BJ" or board(code) != "bse":
        raise ValueError(f"{code} is not a BSE stock")
    return "new" if digits.startswith("920") else "old"
