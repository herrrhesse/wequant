"""The indexes a strategy's scope may name (round 3): the ones offered and kept up to date, and the older ones that
existing strategies still read."""

from __future__ import annotations

OFFERED = ("000300.SH", "000905.SH")  # CSI 300 and CSI 500: BaoStock lists their members, the daily update keeps them
LEGACY = ("000906.SH", "000852.SH", "000985.SH")  # still read where the store has their members, never offered again
INDEXES = (*OFFERED, *LEGACY)  # the codes member@ takes
STORED_AS = {"000300.SH": "399300.SZ"}  # the code an index's rows carry in index_members, where it differs
MEMBER_QUERIES = {"000300.SH": "query_hs300_stocks", "000905.SH": "query_zz500_stocks"}  # BaoStock's member lists


def stored_as(index: str) -> str:
    """The index_code an index's member spans carry."""
    return STORED_AS.get(index, index)
