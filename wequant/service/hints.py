"""Hints (S3.10): the rule an error breaks, in one Chinese sentence that says what is missing and how to fix it, for
the codes that cover several rules (requires, out_of_range, not_a_trading_day, ...). Without one, labels.error_codes'
general sentence for the code says it."""

from __future__ import annotations

import re
from collections.abc import Mapping

ANY = None  # a rule at any field
NO_DAYS = "所选日期范围内没有交易日：请调整开始或结束日期"
NOT_KEPT = "这次回测用的数据版本已经被清理，不能{}；如果导出过那个数据版本，导入后可以再试"
PERIOD_FIELDS = (  # (field name pattern, what it is) of the fields WEEK and MONTH refuse (S4.8)
    (r"st(_known)?", " ST 标记"),
    (r"limit_\w+", "涨跌停"),
    ("list_days", "上市天数"),
    ("delisting", "退市整理期"),
    (r"member@\S+", "指数成分"),
    (r"(board|exchange)@\w+", "板块和交易所"),
    ("factor", "复权因子"),
    ("CODES", "自定义名单"),
)

# (code, field, English message pattern) -> hint. The field is the last name in the error's loc ("" for none), the
# pattern a re.search on its message ("" for any); the first rule that fits wins, so a field's patterns come first.
HINTS: dict[tuple[str, str | None, str], str] = {
    ("requires", "scope", "names no index"): "选了沪深全部，就不用再选指数或名单",
    ("requires", "scope", "needs every stock"): "数据范围至少选一项：沪深全部、沪深300、中证500 或一份股票名单",
    ("requires", "top_n", ""): "限定了持有只数，就需要至少一个启用的打分因子来排名：请添加或启用一个因子，或者选不限",
    ("requires", "backups", ""): "备选只数需要先设持有只数",
    ("requires", "weighting", ""): "按综合分加权需要至少一个启用的打分因子",
    ("requires", "formula", ""): "权重公式只在选了“按公式”加权时填写；选了按公式加权，就要填写公式",
    (
        "requires",
        "hold_rank",
        "needs sell mode",
    ): "持有缓冲只在“调仓时不再入选就卖”时有效：按规则卖出时由规则决定卖什么",
    ("requires", "hold_rank", ""): "持有缓冲需要先设持有只数",
    ("requires", "mode", "needs an int top_n"): "“入选只决定买入”要先设持有只数：空出几个位置才知道要补几只",
    (
        "requires",
        "mode",
        "needs a rule that sells",
    ): "“入选只决定买入”至少要开一条卖出规则（止损、止盈、移动止损、持满卖出或卖出公式），否则持仓永远不卖",
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
    ("out_of_range", "stop_loss", ""): "止损比例要在 0 到 1 之间，例如跌 10% 卖出填 0.1",
    ("out_of_range", "trailing_stop", ""): "移动止损比例要在 0 到 1 之间，例如回落 8% 卖出填 0.08",
    ("out_of_range", "take_profit", ""): "止盈比例要大于 0，例如涨 20% 卖出填 0.2",
    ("out_of_range", "max_hold_days", ""): "持满卖出的天数至少为 1",
    ("out_of_range", "min_hold_days", ""): "最短持有天数至少为 1",
    ("out_of_range", "cooldown_days", ""): "冷却期的天数不能是负数，不要冷却就填 0",
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
    ("no_store", ANY, ""): "这个文件夹里还没有数据：请先完成首次设置，或者改用一个已有的数据目录",
    ("version_not_kept", "data_version", ""): "这个数据版本已经被清理：请选一个还保留着的数据版本",
    ("version_not_kept", "id", ""): NOT_KEPT.format("重跑"),
    ("version_not_kept", "run", ""): NOT_KEPT.format("按它解释"),
    ("bad_file", "name", ""): "文件名要保持导出时的名字：请不要改名后再导入",
    **{
        (
            "period_field",
            ANY,
            rf"^{pattern} cannot be read inside {op}",
        ): f"{unit}里不能用{what}，请把这个条件写在{unit}外面"
        for op, unit in (("WEEK", "周线"), ("MONTH", "月线"))
        for pattern, what in PERIOD_FIELDS
    },
    ("period_nested", ANY, ""): "周线和月线不能嵌套：周线里不能再套周线或月线，月线里也一样",
    ("period_cross_section", ANY, "inside WEEK"): "周线里不能用横截面排名，请写在周线外面，例如 CS_RANK(WEEK(…))",
    ("period_cross_section", ANY, "inside MONTH"): "月线里不能用横截面排名，请写在月线外面，例如 CS_RANK(MONTH(…))",
}


MISSING = {  # wequant.formula.names.NO_DATA's English -> Chinese
    "financial statements": "财务数据",
    "real-time quotes": "实时行情",
    "share capital": "股本数据",
    "market index bars": "大盘指数的行情",
    "advancing and declining counts": "涨跌家数",
    "tick trades": "逐笔成交",
    "chip distribution": "筹码分布",
    "industries and sectors": "行业和板块成分",
    "dates and times": "日期和时间",
    "codes and names": "证券代码和名称",
}
UNIT = {"week": "周线", "month": "月线"}
SEE = "能用的函数和字段见公式帮助"

# (code, English message pattern with named groups) -> Chinese template over those groups, for the rules whose words
# name a part of a formula (S4.1); the first that matches wins.
FORMATS: dict[tuple[str, str], str] = {
    ("formula_syntax", r"never closed"): "注释少了右大括号 }：每个 { 都要有一个 } 结尾",
    (
        "formula_syntax",
        r"unexpected character '#'",
    ): "这里不支持通达信的跨周期写法 X#WEEK：请把公式周期选成周线，或者写 WEEK(X)",
    ("formula_syntax", r"unexpected character '(?P<char>.+)'"): "看不懂字符 {char}：请检查是不是多打了符号",
    ("formula_syntax", r"^expected ; before"): "两行之间少了分号 ;",
    (
        "formula_syntax",
        r"^'(?P<token>.+)' is not a drawing style",
    ): "输出行逗号后面只能写画线属性（如 COLORRED、NODRAW），{token} 不是",
    ("formula_syntax", r"ends where an expression"): "公式在这里就结束了，后面还少一个值或者表达式",
    (
        "formula_syntax",
        r"line (?P<line>\d+), col (?P<col>\d+)",
    ): "少了右括号 )：第 {line} 行第 {col} 列的左括号没有配上",
    ("formula_syntax", r"^unexpected '(?P<token>.+)'"): "这里多了 {token}，或者它前面少了一个值",
    (
        "formula_unknown_name",
        r"^unknown name (?P<name>\S+); did you mean (?P<close>\S+)\?",
    ): "不认识 {name}，你是不是想用 {close}？",
    ("formula_unknown_name", r"^unknown name (?P<name>\S+)"): f"不认识 {{name}}：请检查拼写，{SEE}",
    (
        "formula_unknown_name",
        r"^(?P<name>\S+) is an indicator",
    ): "{name} 是指标，不是函数：可以用 EMA、SMA、HHV、LLV 等函数写出来，例子见公式帮助",
    (
        "formula_unknown_name",
        r"^(?P<name>\S+) is written as an operator",
    ): "{name} 在公式里直接写成运算符：比较写 > < >= <= = <>，组合写 AND、OR",
    ("formula_unknown_name", r"^(?P<name>\S+) is a function"): "{name} 是函数，要带括号和参数，例如 {name}(CLOSE, 5)",
    (
        "formula_unknown_name",
        r"^(?P<name>\S+) is used before line (?P<line>\d+)",
    ): "{name} 到第 {line} 行才定义：要先定义再用",
    ("formula_no_output", r""): "公式没有输出：最后一行写成“XG: 条件;”，或者直接写一个不带名字的表达式",
    (
        "formula_not_constant",
        r"^(?P<name>\S+)'s period",
    ): "{name} 的周期参数要是常数，例如先写 N:=9; 再写 {name}(CLOSE, N)",
    ("formula_no_data", r"^(?P<name>\S+) needs (?P<what>.+), which"): "{name} 要用{what}，这份数据里没有",
    ("formula_unsupported", r"^(?P<name>\S+) is a drawing function"): "{name} 是画线函数，选股用不上：请删掉这一行",
    ("formula_unsupported", r"^(?P<name>\S+) is not supported"): f"暂时不支持 {{name}}：{SEE}",
    (
        "formula_needs_bool",
        r"^(?P<name>\S+) is a number",
    ): "这里要的是条件，但输出 {name} 是数值：请写成比较，例如 {name} > 0",
    ("formula_redefined", r"^(?P<name>\S+) is defined twice"): "{name} 定义了两次：请给其中一个换个名字",
    ("formula_too_long", r"has (?P<n>\d+) characters, more than (?P<max>\d+)"): "公式太长了：{n} 个字符，最多 {max} 个",
    (
        "future_function",
        r"^(?P<name>\S+) is a future function",
    ): "{name} 是未来函数，会用到决策日之后的数据：选股和回测里不能用",
    (
        "needs_series",
        r"^both sides of (?P<op>\S+) are constants",
    ): "{op} 两边都是常数：至少一边要是随日期变化的量，例如 CLOSE",
    ("needs_series", r"^a constant (?P<value>\S+) where a series"): "这里要一个随日期变化的量，不能只写常数 {value}",
    (
        "needs_series",
        r"does not take a quoted name|cannot be computed with",
    ): "引号里的名字只能写在 MEMBER、BOARD、EXCHANGE、CODES 里",
    (
        "bad_arity",
        r"^(?P<name>\S+) takes (?P<n>\d+) arguments?, not (?P<m>\d+)",
    ): "{name} 要 {n} 个参数，这里写了 {m} 个",
    ("bad_arity", r"^(?P<name>\S+) takes one quoted name"): "{name} 要一个带引号的名字，例如 {name}('000300.SH')",
    ("bad_arity", r"^CODES takes quoted codes"): "CODES 要带引号的代码，例如 CODES('600000.SH', '000001.SZ')",
    ("bad_constant", r"divided by 0"): "常数除以 0：请检查分母",
    (
        "period_field",
        r"^(?P<name>\S+) cannot be read in a (?P<period>week|month)ly formula",
    ): "{unit}公式里不能用 {name}：{unit}只有开高低收、成交量和成交额，请把这个条件单独写成日线条件",
    (
        "period_nested",
        r"^(?P<name>\S+) cannot be used in a (?P<period>week|month)ly formula",
    ): "公式周期已经是{unit}，里面不能再写 WEEK 或 MONTH",
    (
        "period_cross_section",
        r"^the cross-sectional (?P<name>\S+) cannot be used in a (?P<period>week|month)ly formula",
    ): "{unit}公式里不能用横截面排名 {name}：请把排名单独写成日线因子，例如 CS_RANK(WEEK(…))",
    (
        "adjusted_level",
        r"constant (?P<value>\S+)$",
    ): "价格是后复权价，和固定价位 {value} 比会偏离实际价格；比较绝对价位请用 RAW_CLOSE（不复权收盘价）",
    ("formula_shadows", r"^(?P<name>\S+) is a built-in name"): "{name} 本来是内置名字，这一行以后 {name} 指这个变量",
}


def hint(detail: Mapping) -> str | None:
    """The hint of the rule an error detail's code, loc and English message tell; None without one."""
    code, loc, message = detail.get("code", ""), detail.get("loc", []), detail.get("message", "")
    field = next((part for part in reversed(loc) if isinstance(part, str)), "")
    rules = ((f, pattern, text) for (c, f, pattern), text in HINTS.items() if c == code)
    found = next((text for f, pattern, text in rules if f in (field, ANY) and re.search(pattern, message)), None)
    return found or _formatted(code, message)


def _formatted(code: str, message: str) -> str | None:
    """The FORMATS hint of a code and message, filled from the message's words."""
    for (rule, pattern), template in FORMATS.items():
        if rule == code and (match := re.search(pattern, message)):
            words = match.groupdict()
            period = words.get("period")
            words |= {"unit": UNIT.get(period, ""), "what": MISSING.get(words.get("what", ""), words.get("what"))}
            return template.format(**words)
    return None
