"""Hints (S3.10): the rule an error breaks, in one Chinese sentence that says what is missing and how to fix it, for
the codes that cover several rules (requires, out_of_range, not_a_trading_day, ...). Without one, labels.error_codes'
general sentence for the code says it."""

from __future__ import annotations

import re
from collections.abc import Mapping

ANY = None  # a rule at any field
NO_DAYS = "所选日期范围内没有交易日：请调整开始或结束日期"
NOT_KEPT = "这次回测用的数据版本已经被清理，不能{}；如果导出过那个数据版本，导入后可以再试"

# (code, field, English message pattern) -> hint. The field is the last name in the error's loc ("" for none), the
# pattern a re.search on its message ("" for any); the first rule that fits wins, so a field's patterns come first.
HINTS: dict[tuple[str, str | None, str], str] = {
    ("requires", "top_n", ""): "限定了持有只数，就需要至少一个启用的打分因子来排名：请添加或启用一个因子，或者选不限",
    ("requires", "backups", ""): "备选只数需要先设持有只数",
    ("requires", "weighting", ""): "按综合分加权需要至少一个启用的打分因子",
    ("requires", "formula", ""): "权重公式只在选了“按公式”加权时填写；选了按公式加权，就要填写公式",
    ("requires", "hold_rank", ""): "持有缓冲需要先设持有只数",
    ("requires", "failure_recovery", ""): "减记回收比例只在退市处理选“按原因自动”或“减记”时有效",
    ("requires", "exclude_suspicious", ""): "同时剔除可疑数据只在异常数据选“剔除当天的异常数据”时有效",
    ("requires", "stocks", ""): "指数和自定义名单只能选一个：请去掉其中一个",
    ("requires", "data", ""): "按一次回测解释时，异常数据的处理取自那次回测，请不要另外设置",
    ("requires", "data_version", ""): "按一次回测解释时，数据版本取自那次回测，请不要另外指定",
    ("requires", "", "^give exactly one of strategy and saved"): "请二选一：直接给出策略，或者选一个已保存的策略",
    ("requires", "", "^give exactly one of strategy, saved and run"): "请三选一：给出策略、选已保存的策略或选一次回测",
    ("requires", "", "^select entries"): "请用代码、日期、日期范围、种类或类别中的至少一个选出要处理的条目",
    ("out_of_range", "top_n", ""): "持有只数至少为 1",
    ("out_of_range", "backups", ""): "备选只数不能是负数",
    ("out_of_range", "every", ""): "调仓间隔至少为 1 个周期",
    ("out_of_range", "band", ""): "偏离阈值不能是负数",
    ("out_of_range", "hold_rank", ""): "持有缓冲要大于持有只数：已持有的股票排名在缓冲以内就继续持有",
    ("out_of_range", "max_weight", ""): "单股上限要大于 0、小于 100%；不设就是不限",
    ("out_of_range", "boards", ""): "板块至少选一个；全选等于不限",
    ("out_of_range", "min_list_days", ""): "剔除次新股的上市天数至少填 2",
    ("out_of_range", "weight", ""): "因子权重要大于 0",
    ("out_of_range", "conditions", ""): "条件组里至少要有一个条件",
    ("out_of_range", "name", ""): "名称不能为空",
    ("out_of_range", "commission_rate", ""): "佣金率要大于等于 0、小于 1，例如万分之三填 0.0003",
    ("out_of_range", "min_commission", ""): "最低佣金不能是负数，并且要精确到分",
    ("out_of_range", "slippage_bps", ""): "滑点按基点计（1 基点是万分之一），要大于等于 0、小于 10000",
    ("out_of_range", "failure_recovery", ""): "减记回收比例要在 0 到 1 之间，例如收回一半填 0.5",
    ("out_of_range", "cash", ""): "初始资金要大于 0，并且精确到分",
    ("out_of_range", "periods_per_year", ""): "每年的周期数至少为 1",
    ("out_of_range", "tail_fraction", ""): "尾部比例要大于 0、小于 1",
    ("out_of_range", "runs_limit_gb", ""): "回测记录的空间上限要大于 0 GB",
    ("out_of_range", "keep_versions", ""): "至少要保留 1 个数据版本",
    ("out_of_range", "codes", ""): "至少要选一个代码",
    ("out_of_range", "fields", ""): "至少要选一个字段",
    ("out_of_range", ANY, "is after end"): "开始日期不能晚于结束日期",
    ("out_of_range", ANY, "is before the panel's first day"): "开始日期早于数据的第一个交易日：请改到数据范围之内",
    ("out_of_range", ANY, "is before the strategy's anchor"): "开始日期早于策略的调仓锚点日：请改到锚点日当天或之后",
    ("out_of_range", "end", "^no run day"): "回测区间太短：开始和结束日期之间至少要有两个交易日",
    ("out_of_range", ANY, r"^argument \d+ of \S+ must be at least"): "函数的周期参数太小：请改大到这个函数允许的最小值",
    ("out_of_range", ANY, r"^argument \d+ of \S+ must be at most"): "函数的周期参数太大：请改小",
    ("out_of_range", ANY, r"must be an integer constant from 1"): "权重参数要是 1 到周期之间的整数",
    ("not_a_trading_day", "anchor", ""): "调仓锚点日要是所用数据里的一个交易日：请改成数据范围内的一个交易日",
    ("not_a_trading_day", "date", "is not a day of run"): "这一天不在这次回测里：请选回测期间的一个交易日",
    ("not_a_trading_day", "date", ""): "这一天不是交易日：请选一个交易日",
    ("not_a_trading_day", ANY, "^no trading day in"): NO_DAYS,
    ("not_available", ANY, "^turn is not available"): "换手率要等 BaoStock 数据正式合并（S2.4b）以后才有",
    ("no_data", "index", ""): "这个数据库里没有这个指数的成分数据：请先更新数据，或者换一个指数",
    ("no_data", "benchmark", ""): "基准指数在回测期间没有收盘价：请换一个基准，或者不设基准",
    ("wrong_type", "benchmark", ""): "基准要填一个指数代码（例如 000300.SH），或者不设基准",
    ("wrong_type", "name", "^String should have at least 1 character"): "名称不能为空",
    ("wrong_type", ANY, "^String should have at least 1 character"): "这一项不能为空",
    ("wrong_type", ANY, "not a date|a valid date"): "日期要写成“年-月-日”，例如 2024-01-31，不带时间",
    ("wrong_type", ANY, "not a wequant code|is not a code such as"): "代码要写成 6 位数字加交易所，例如 600000.SH",
    ("version_not_kept", "data_version", ""): "这个数据版本已经被清理：请选一个还保留着的数据版本",
    ("version_not_kept", "id", ""): NOT_KEPT.format("重跑"),
    ("version_not_kept", "run", ""): NOT_KEPT.format("按它解释"),
    ("bad_file", "name", ""): "文件名要保持导出时的名字：请不要改名后再导入",
}


def hint(detail: Mapping) -> str | None:
    """The hint of the rule an error detail's code, loc and English message tell; None without one."""
    code, loc, message = detail.get("code", ""), detail.get("loc", []), detail.get("message", "")
    field = next((part for part in reversed(loc) if isinstance(part, str)), "")
    rules = ((f, pattern, text) for (c, f, pattern), text in HINTS.items() if c == code)
    return next((text for f, pattern, text in rules if f in (field, ANY) and re.search(pattern, message)), None)
