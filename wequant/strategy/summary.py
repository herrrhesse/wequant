"""A strategy in one Chinese sentence, fragment by fragment, each pointing at the fields it describes (design 10.1)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

from wequant.strategy.section import weight_shares
from wequant.strategy.strategy import SH_SZ, Condition, Strategy

INDEX_NAMES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000906.SH": "中证800",
    "000852.SH": "中证1000",
    "000985.SH": "中证全指",
}
BOARD_NAMES = {"main": "主板", "chinext": "创业板", "star": "科创板"}
PERIOD_NAMES = {"day": "个交易日", "week": "周最后一个交易日", "month": "个月最后一个交易日"}
EVERY_ONE = {"day": "每个交易日", "week": "每周最后一个交易日", "month": "每月最后一个交易日"}


class Part(NamedTuple):
    """One fragment: the fields it describes, its text without punctuation, and its numbers."""

    loc: tuple[tuple[str, ...], ...]
    text: str
    params: dict[str, int | float | str]


def sentence(parts: list[Part]) -> str:
    """The fragments joined by commas, a parenthesised one attached to the one before, then a full stop."""
    return "".join(("" if i == 0 or p.text.startswith("（") else "，") + p.text for i, p in enumerate(parts)) + "。"


def summary(strategy: Strategy) -> list[Part]:
    """The fragments of the sentence in order; unset and disabled items leave theirs out."""
    return list(_parts(strategy))


def _parts(s: Strategy) -> Iterator[Part]:
    yield _scope(s)
    if s.pool.boards:
        names = [BOARD_NAMES[b] for b in s.pool.boards]
        yield Part((("pool", "boards"),), f"（仅{_and(names)}）", {})
    yield _excluded(s)
    yield _filters(s)
    if shares := weight_shares(s):
        yield _scoring(s, shares)
    yield _holding(s)
    if s.hold_rank is not None:
        yield Part((("hold_rank",),), f"持有中的股票排名跌出前{s.hold_rank}才卖出", {"hold_rank": s.hold_rank})
    yield _weighting(s)
    if s.max_weight is not None:
        yield Part((("max_weight",),), f"单股不超过{_pct(s.max_weight)}", {"max_weight": s.max_weight})
    if s.band is not None:
        yield Part((("band",),), f"权重偏离超过{_pct(s.band)}才调整", {"band": s.band})
    every = EVERY_ONE[s.period] if s.every == 1 else f"每{s.every}{PERIOD_NAMES[s.period]}"
    yield Part((("period",), ("every",)), f"{every}调仓", {"every": s.every})
    if s.anchor is not None:
        yield Part((("anchor",),), f"（指标从{s.anchor.isoformat()}起算）", {"anchor": s.anchor.isoformat()})


def _scope(s: Strategy) -> Part:
    custom = s.universe != SH_SZ
    index = INDEX_NAMES.get(s.pool.index)
    if index and custom:
        return Part((("pool", "index"), ("universe",)), f"在{index}成分股中、满足自定义范围的股票里", {})
    if index:
        return Part((("pool", "index"),), f"在{index}成分股中", {})
    return Part((("universe",),), "在自定义范围内" if custom else "在沪深全部股票中", {})


def _excluded(s: Strategy) -> Part:
    items, loc, params = ["停牌股"], [], {}
    if s.pool.exclude_st:
        items.append("ST 股")
        loc.append(("pool", "exclude_st"))
    if s.pool.min_list_days is not None:
        items.append(f"上市不足{s.pool.min_list_days}个交易日的次新股")
        loc.append(("pool", "min_list_days"))
        params["min_list_days"] = s.pool.min_list_days
    return Part(tuple(loc), "剔除" + "、".join(items), params)


def _filters(s: Strategy) -> Part:
    names = []
    for item in (f for f in s.filters if f.enabled):
        if isinstance(item, Condition):
            names.append(item.name)
        elif members := [c.name for c in item.conditions if c.enabled]:
            names.append(f"（{('且' if item.mode == 'all' else '或').join(members)}）")
    return Part((("filters",),), f"选出{'、'.join(names)}的股票" if names else "不设筛选条件", {})


def _scoring(s: Strategy, shares: dict[str, float]) -> Part:
    prefer = {f.name: f.prefer for f in s.factors}
    if len(shares) == 1:
        (name,) = shares
        text = name if prefer[name] == "high" else f"{name}（越小越好）"
    else:
        text = "、".join(
            f"{n}（{_pct(x)}，{'越大越好' if prefer[n] == 'high' else '越小越好'}）" for n, x in shares.items()
        )
    return Part((("factors",),), f"按{text}打分", dict(shares))


def _holding(s: Strategy) -> Part:
    if s.top_n is None:
        return Part((("top_n",),), "持有全部入选股票", {})
    if s.backups:
        return Part(
            (("top_n",), ("backups",)),
            f"持有前{s.top_n}只，备选{s.backups}只",
            {"top_n": s.top_n, "backups": s.backups},
        )
    return Part((("top_n",),), f"持有前{s.top_n}只", {"top_n": s.top_n})


def _weighting(s: Strategy) -> Part:
    if s.weighting == "formula":
        return Part((("weighting",), ("formula",)), "按公式加权", {})
    return Part((("weighting",),), "等权" if s.weighting == "equal" else "按得分加权", {})


def _pct(share: float) -> str:
    """A share as a percentage with at most one decimal and no trailing zero: 0.15 -> 15%, 1/3 -> 33.3%."""
    return f"{share * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else "、".join(names[:-1]) + "和" + names[-1]
