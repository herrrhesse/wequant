"""strategy templates (S3.8, R-03): starting points for a new strategy, built from prices and volumes only, each with a
plain Chinese name and what it does, when it may work and when it may not; no returns are promised or shown."""

from __future__ import annotations

from wequant.service.base import Context, Model
from wequant.service.indicators import DERIVED
from wequant.service.specs import StrategySpec
from wequant.strategy import Strategy
from wequant.strategy.summary import sentence, summary

FORM = {id: entry[4] for id, entry in DERIVED.items()}  # the common indicators' forms, by id
LISTED = {"exclude_st": True, "min_list_days": 60}  # leave out ST stocks and those listed under about three months

TEMPLATES: dict[str, tuple[str, str, dict]] = {  # id -> (Chinese name, description, strategy form)
    "momentum": (
        "20 日动量",
        "买入最近 20 个交易日涨得最多的 20 只股票，每周调仓。趋势延续的行情里可能有效；"
        "行情突然反转时，前面涨得多的股票往往跌得也多。",
        {
            "pool": LISTED,
            "factors": [{"name": "20 日涨跌幅", "expr": FORM["change_20"]}],
            "top_n": 20,
            "period": "week",
        },
    ),
    "low_volatility": (
        "低波动",
        "买入最近 60 个交易日涨跌最平稳的 30 只股票，每月调仓。市场震荡或下跌时可能更抗跌；"
        "单边快速上涨的行情里往往涨得比大盘慢。",
        {
            "pool": {"exclude_st": True, "min_list_days": 120},
            "factors": [{"name": "60 日波动率", "expr": FORM["volatility_60"], "prefer": "low"}],
            "top_n": 30,
            "period": "month",
        },
    ),
    "short_reversal": (
        "短期反转",
        "在成交比较活跃的股票里，买入最近 5 个交易日跌得最多的 20 只，每周调仓，等短期超跌后的反弹。"
        "震荡市里可能有效；遇到一路下跌的股票会继续亏，换手也比较高。",
        {
            "pool": LISTED,
            "filters": [{"name": "成交活跃", "expr": ["GT", FORM["avg_amount_20"], 50_000_000.0]}],
            "factors": [{"name": "5 日涨跌幅", "expr": FORM["change_5"], "prefer": "low"}],
            "top_n": 20,
            "period": "week",
        },
    ),
    "liquid_momentum": (
        "成交活跃的动量股",
        "先挑出最近 20 个交易日平均成交额超过 1 亿元的股票，再买其中最近 60 个交易日涨得最多的 20 只，每周调仓。"
        "只买成交活跃的股票，买卖更容易成交；趋势反转时一样会回撤。",
        {
            "pool": LISTED,
            "filters": [{"name": "日均成交额过亿", "expr": ["GT", FORM["avg_amount_20"], 100_000_000.0]}],
            "factors": [{"name": "60 日涨跌幅", "expr": FORM["change_60"]}],
            "top_n": 20,
            "period": "week",
        },
    ),
    "trend_momentum": (
        "长期均线之上的动量股",
        "只看收盘价在 120 日均线之上的股票，买其中最近 20 个交易日涨得最多的 20 只，每周调仓。"
        "均线把处在下跌趋势里的股票挡在外面；行情来回震荡时可能频繁进出。",
        {
            "pool": LISTED,
            "filters": [{"name": "在 120 日均线之上", "expr": ["GT", "adj_close", ["MA", "adj_close", 120]]}],
            "factors": [{"name": "20 日涨跌幅", "expr": FORM["change_20"]}],
            "top_n": 20,
            "period": "week",
        },
    ),
    "csi300_low_volatility": (
        "沪深300里的低波动",
        "在沪深300成分股里，买最近 60 个交易日涨跌最平稳的 30 只，每月调仓。大盘股成交活跃、波动较小；"
        "市场偏爱小盘股或单边上涨时可能落后。",
        {
            "pool": {"index": "000300.SH", "exclude_st": True},
            "factors": [{"name": "60 日波动率", "expr": FORM["volatility_60"], "prefer": "low"}],
            "top_n": 30,
            "period": "month",
        },
    ),
}


class TemplatesRequest(Model):
    """Every strategy template."""


class Template(Model):
    """A starting point for a new strategy."""

    id: str
    name: str
    """Chinese, plain."""
    description: str
    """Two or three Chinese sentences: what it does, when it may work, when it may not."""
    strategy: StrategySpec
    """The whole strategy, to open in the editor; its scope is one labels.scopes offers."""
    summary: str
    """The strategy's Chinese summary sentence."""


class Templates(Model):
    """The strategy templates, in the order to show."""

    rows: list[Template]


def strategy_templates(request: TemplatesRequest, context: Context = Context()) -> Templates:
    """The strategy templates, each a whole valid strategy with its summary."""
    rows = []
    for id, (name, description, form) in TEMPLATES.items():
        strategy = Strategy.from_form(form)
        spec = StrategySpec.model_validate(strategy.form)
        rows.append(
            Template(id=id, name=name, description=description, strategy=spec, summary=sentence(summary(strategy)))
        )
    return Templates(rows=rows)
