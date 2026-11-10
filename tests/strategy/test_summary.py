import datetime as dt
from dataclasses import replace

import pytest

from wequant.expr import Field
from wequant.strategy import Condition, Factor, Group, Pool, Strategy
from wequant.strategy.section import weight_shares
from wequant.strategy.summary import sentence, summary

C = Field("close")
BASE = Strategy()
BASE_TEXT = "在沪深全部股票中，剔除停牌股，不设筛选条件，持有全部入选股票，等权，每周最后一个交易日调仓。"


def condition(name):
    return Condition(name, C > 1.0)


# Design 10.2, word for word: the report's M and S strategies are rebuilt here so the test reads no tool.
EXAMPLES = [
    (
        Strategy(
            filters=(condition("20日均成交额大于1亿"), condition("近60日波动率排名后50%")),
            factors=(Factor("动量", C), Factor("低波", C, "low")),
            top_n=20,
            pool=Pool(index="000906.SH", exclude_st=True, min_list_days=250),
        ),
        "在中证800成分股中，剔除停牌股、ST 股、上市不足250个交易日的次新股，选出20日均成交额大于1亿、近60日波动率排名后50%"
        "的股票，按动量（50%，越大越好）、低波（50%，越小越好）打分，持有前20只，等权，每周最后一个交易日调仓。",
    ),
    (
        Strategy(
            filters=(
                condition("liquid"),
                condition("seasoned"),
                Group("tradable", (condition("no_st"), condition("not_up"))),
            ),
            factors=(Factor("momentum", C), Factor("low_vol", C, "low")),
            top_n=50,
            backups=10,
        ),
        "在沪深全部股票中，剔除停牌股，选出liquid、seasoned、（no_st且not_up）的股票，按momentum（50%，越大越好）、"
        "low_vol（50%，越小越好）打分，持有前50只，备选10只，等权，每周最后一个交易日调仓。",
    ),
    (
        Strategy(
            filters=(condition("liquid"), condition("not_st")),
            factors=(Factor("cheap", C, "low"), Factor("reversal", C, "low")),
            top_n=30,
            hold_rank=45,
            period="month",
            weighting="score",
            max_weight=0.08,
            band=0.2,
            anchor=dt.date(2016, 11, 7),
        ),
        "在沪深全部股票中，剔除停牌股，选出liquid、not_st的股票，按cheap（50%，越小越好）、reversal（50%，越小越好）打分，"
        "持有前30只，持有中的股票排名跌出前45才卖出，按得分加权，单股不超过8%，权重偏离超过20%才调整，"
        "每月最后一个交易日调仓（指标从2016-11-07起算）。",
    ),
    (
        Strategy(
            factors=(Factor("20日涨幅", C),), top_n=10, every=2, pool=Pool(boards=("main", "chinext"), exclude_st=True)
        ),
        "在沪深全部股票中（仅主板和创业板），剔除停牌股、ST 股，不设筛选条件，按20日涨幅打分，持有前10只，等权，"
        "每2周最后一个交易日调仓。",
    ),
    (
        Strategy(
            universe=C > 2.0,
            filters=(Group("g", (condition("站上60日线"), condition("放量突破")), "any"),),
            period="day",
            weighting="formula",
            formula=C,
        ),
        "在自定义范围内，剔除停牌股，选出（站上60日线或放量突破）的股票，持有全部入选股票，按公式加权，每个交易日调仓。",
    ),
]


@pytest.mark.parametrize("strategy, text", EXAMPLES, ids=[f"example {i}" for i in range(1, 6)])
def test_the_design_examples(strategy, text):
    assert sentence(summary(strategy)) == text


ONE_FACTOR = Strategy(factors=(Factor("f", C),))
FIELDS = [
    (replace(BASE, pool=Pool(index="000300.SH")), [("pool", "index")], "在沪深300成分股中"),
    (
        replace(BASE, universe=C > 2.0, pool=Pool(index="000852.SH")),
        [("pool", "index"), ("universe",)],
        "在中证1000成分股中、满足自定义范围的股票里",
    ),
    (replace(BASE, pool=Pool(boards=("main", "chinext", "star"))), [("pool", "boards")], "（仅主板、创业板和科创板）"),
    (replace(BASE, pool=Pool(min_list_days=60)), [("pool", "min_list_days")], "剔除停牌股、上市不足60个交易日的次新股"),
    (replace(BASE, filters=(condition("a"), replace(condition("b"), enabled=False))), [("filters",)], "选出a的股票"),
    (replace(ONE_FACTOR, factors=(Factor("f", C, "low"),)), [("factors",)], "按f（越小越好）打分"),
    (
        replace(ONE_FACTOR, factors=(Factor("f", C), Factor("g", C, weight=2.0), Factor("h", C, enabled=False))),
        [("factors",)],
        "按f（33.3%，越大越好）、g（66.7%，越大越好）打分",
    ),
    (replace(ONE_FACTOR, top_n=5), [("top_n",)], "持有前5只"),
    (replace(ONE_FACTOR, weighting="score"), [("weighting",)], "按得分加权"),
    (replace(BASE, max_weight=0.125), [("max_weight",)], "单股不超过12.5%"),
    (replace(BASE, period="day", every=5), [("period",), ("every",)], "每5个交易日调仓"),
    (replace(BASE, period="month", every=3), [("period",), ("every",)], "每3个月最后一个交易日调仓"),
]


@pytest.mark.parametrize("strategy, loc, text", FIELDS, ids=[case[2] for case in FIELDS])
def test_each_field_set_alone_shows_in_its_fragment(strategy, loc, text):
    assert (tuple(loc), text) in [(p.loc, p.text) for p in summary(strategy)]


def test_unset_items_leave_their_fragments_out():
    assert sentence(summary(BASE)) == BASE_TEXT


def test_the_scoring_fragment_carries_the_weight_shares():
    strategy = Strategy(factors=(Factor("f", C, weight=1.0), Factor("g", C, weight=3.0)))
    (scoring,) = [p for p in summary(strategy) if p.loc == (("factors",),)]
    assert scoring.params == weight_shares(strategy) == {"f": 0.25, "g": 0.75}
