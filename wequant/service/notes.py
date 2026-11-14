"""The page's sentences for a pool item on one stock and day (S2.10a): shared by the decision chain and the screen's
rejects."""

from __future__ import annotations

from wequant.panel import Panel

LIST_DAYS_NOTE = "这只股票不知道上市日期或在数据的日历之前上市，上市天数按下限估算"  # shown on the page (F-31)
DELISTING_NOTE = "处于退市整理期（第 {} 天）"  # k/N of its delisting period


def delisting_day(panel: Panel, row: int, col: int) -> str | None:
    """ "k/N" on the k-th of the N days of the stock's delisting period; None outside it."""
    day = int(panel.delisting[row, col])
    return f"{day}/{int(panel.delisting_days[col])}" if day else None


def pool_note(name: str, panel: Panel, row: int, col: int) -> str | None:
    """The sentence for pool item name: list days counted by a lower bound, the day of a delisting period; None."""
    if name == "min_list_days" and panel.list_days_bound[col]:
        return LIST_DAYS_NOTE
    if name == "exclude_delisting" and (day := delisting_day(panel, row, col)) is not None:
        return DELISTING_NOTE.format(day)
    return None
