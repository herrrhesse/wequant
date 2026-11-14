"""labels: the Chinese name and sentence of every code the operations return (F-19, R-10, R-11). English stays in the
text fields, for logs and the command line. A test keeps these tables and the codes in the code base one to one."""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from typing import Literal

from wequant.data.schema import ISSUE_KINDS
from wequant.service.base import Context, Model

# ---------------------------------------------------------------- tables: code -> (Chinese name, Chinese sentence)

WARNINGS = {  # code -> (name, template, optional clauses by param, repeat separator)
    "limits_unchecked": ("涨跌停未核对", "这个数据版本没有核对过涨跌停价，判断涨跌停的结果可能不准", {}, None),
    "st_unknown": ("ST 状态缺失", "决策日的股票里只有 {known_share:.1%} 知道是否为 ST，不知道的按非 ST 处理", {}, None),
    "st_band_fills": (
        "按 ST 涨跌幅成交",
        "不知道是否 ST 的股票里，有 {buys} 笔买入按 ST 涨停价、{sells} 笔卖出按 ST 跌停价成交",
        {},
        None,
    ),
    "stale_holdings": (
        "持有停牌过久的股票",
        "{codes} 只股票在最后一根 K 线之后又持有了 {stale_days} 个交易日以上，按最后收盘价估值：最多时占权益 "
        "{worst_share:.1%}（{worst_day}），最后一天 {day} 占 {share:.1%}",
        {},
        None,
    ),
    "no_picks": ("没有选出股票", "所有决策日都没有选出股票", {}, None),
    "legacy_index": ("旧的范围选项", "{name}是旧的范围选项，新建策略不再提供；这个策略照样能校验和运行", {}, None),
    "stocks_not_in_data": (
        "名单里有数据中没有的股票",
        "自定义名单里有 {count} 只股票不在这个数据库里：{codes}；选股时它们不会入选",
        {},
        None,
    ),
    "index_uncovered": (
        "指数成分数据不全",
        "{index}：{days} 个决策日不在成分股数据覆盖的范围内",
        {"first": "（成分数据从 {first} 开始）"},
        "；",
    ),
    "list_days_approximate": (
        "上市天数是估算的",
        "上市天数从数据的第一天算起，不是真正的上市日期：数据从日历首日开始的 {codes_at_calendar_start} 只股票偏少，"
        "最少上市天数因此排除了 {cells} 个决策日的股票",
        {},
        None,
    ),
    "max_weight_cash": (
        "单股上限留下现金",
        "{days} 个决策日因为单股权重上限，平均有 {mean_share:.1%} 的权益留作现金",
        {},
        None,
    ),
    "delisted_holdings": (
        "持仓退市",
        "{codes} 只持仓在退市日开盘时清掉，按最后收盘价值 {amount:,.2f} 元，最大一笔占前一日权益的 {max_share:.1%}；"
        "其中 {mergers} 只归为吸收合并或私有化",
        {},
        None,
    ),
    "delisting_approximate": (
        "退市日是估算的",
        "退市日取自证券表的最后交易日，不是官方日期：{earlier_codes} 只持仓在它之后的第一个交易日按零减记，可能早于官方退市，"
        "也可能只是停牌；其中 {maybe_suspended} 只看不出是强制退市",
        {},
        None,
    ),
    "data_anomaly": (
        "用到了异常数据",
        "用到 {codes} 只股票的 {cells} 个异常数据（错误 {error_cells}、可疑 {suspicious_cells}、提示 {notice_cells}）："
        "{decisions} 次决策在信号窗口里读到，{fills} 笔订单落在上面",
        {
            "max_share_day": "；持仓最高时占权益 {max_share:.1%}（{max_share_day}）",
            "unavoidable": "；被排除的级别里还有 {unavoidable} 处用到的数据无法靠剔除当天去掉",
        },
        None,
    ),
    "strategy_version_changed": (
        "策略格式版本变了",
        "这个版本按策略格式 {saved} 保存，现在按格式 {current} 读取，含义可能有变化",
        {},
        None,
    ),
    "meta_rebuilt": ("策略索引已重建", "meta.json 和 {versions} 个版本文件不一致，已按版本文件读取", {}, None),
}

ISSUE_KIND_TEXT = {
    "own_row_dropped": (
        "自备数据的行没有导入",
        "自备数据里这一行的数字读不出来，或者和前面一行的代码、日期重复，没有导入",
    ),
    "own_price_order": (
        "自备数据价格不合理",
        "自备数据里这根 K 线的开高低收对不上（有价格不大于 0，或最高价低于其他价格、最低价高于其他价格），按原样导入",
    ),
    "off_scale": ("量额与价格不符", "种子数据的成交量、成交额和价格对不上，这根 K 线没有导入"),
    "removed_security": ("整只股票被删除", "这只股票每根 K 线都量额与价格不符，整只没有导入"),
    "adj_blip": ("复权价尖峰", "种子数据的复权价出现尖峰，复权因子和推算的昨收绕开了它"),
    "invalid_member_code": ("成分代码无效", "指数成分里的这个代码不是股票，这段成分记录没有导入"),
    "bar_added": ("仅 BaoStock 有的 K 线", "这根 K 线只有 BaoStock 有，整根只有一个来源"),
    "bar_unconfirmed": ("BaoStock 缺的 K 线", "种子有、BaoStock 没有的 K 线，已删除，这一天按缺数据处理"),
    "duplicate_code": ("重复的代码", "新代码复制了旧代码的 K 线，重复的那份已删除"),
    "price_mismatch": ("价格不一致（已改正）", "两个来源的开高低收不一致，按批准的处理和核实记录改正了"),
    "average_price": (
        "均价超出区间（已处理）",
        "成交额除以成交量落在当天最高最低价之外，按批准的处理取种子、置空或改正",
    ),
    "amount_carried": (
        "成交额沿用前一天（已改正）",
        "BaoStock 的成交额照搬了前一根 K 线而成交量不同，按批准的处理改正了",
    ),
    "stale_volume": ("停牌日的旧成交量（已处理）", "停牌那天的行带着旧的成交量，按批准的处理不建 K 线"),
    "volume_truncated": ("快照截断（已改正）", "BaoStock 当天的快照在收盘前截断，按批准的处理改正了成交量和成交额"),
    "preclose_odd": ("昨收不是整分（已处理）", "BaoStock 的交易所昨收不是整分，按批准的处理保留或改正"),
    "price_noted": ("价格已核实，仅记录", "按批准的处理定了价格，核实后保留的就是对的值，只做记录"),
    "price_unverified": ("价格未经外部核实", "按批准的处理定了价格，只有内部证据，没有外部来源核对"),
    "volume_noted": ("量额已核实，仅记录", "按批准的处理定了成交量或成交额，核实后保留的就是对的值，只做记录"),
    "volume_unverified": ("量额未经外部核实", "按批准的处理定了成交量或成交额，只有内部证据，没有外部来源核对"),
    "close_preclose_off": ("收盘价与次日昨收不符", "收盘价不等于下一交易日的交易所昨收，中间也没有除权，收盘价可疑"),
    "snapshot_truncated": (
        "当天快照疑似截断",
        "当天很多收盘价对不上次日昨收，数据源的快照可能在收盘前截断，量额可能偏少",
    ),
    "volume_mismatch": ("成交量不一致", "两个来源的成交量相差超过一手，保留 BaoStock 的值"),
    "amount_mismatch": ("成交额不一致", "两个来源的成交额不一致，保留 BaoStock 的值"),
    "preclose_mismatch": ("昨收不一致", "BaoStock 的交易所昨收和库里原来的不同，已替换"),
    "tradestatus_conflict": ("交易状态与 K 线矛盾", "交易状态说停牌却有 K 线，或说交易却没有 K 线"),
    "before_first_bar": ("首根 K 线之前的状态", "这只股票第一根 K 线之前的状态行，已删除"),
    "event_unconfirmed": ("未证实的除权事件", "种子有、BaoStock 没有的除权事件，已删除"),
    "event_added": ("新增的除权事件", "只有 BaoStock 有的除权事件"),
    "step_mismatch": ("除权幅度不一致", "两个来源的复权跳变幅度不同，已替换"),
    "event_missing": ("复权因子缺事件", "交易所昨收显示这天除权，复权因子里却没有"),
    "event_unimplied": ("复权跳变没有依据", "复权因子在这天跳变，交易所昨收却看不出除权"),
    "event_step_off": ("复权跳变幅度不符", "复权因子的跳变幅度和交易所昨收推出的不一样"),
    "no_bars": ("没有 K 线的代码", "这个代码没有任何 K 线，已从证券表删除"),
    "limit_breach": ("超出涨跌停", "这根 K 线超出了它的涨跌停价，当天的涨跌停按未知处理"),
}

ISSUE_ACTION_TEXT = {
    "row_dropped": ("删除该行", "这行数据没有进库；除非状态表说停牌，这天按缺数据处理"),
    "security_removed": ("删除该代码", "这个代码从证券表删除"),
    "excluded_from_adj_factor_and_preclose": ("复权时绕开", "算复权因子和推算昨收时跳过这个值"),
    "span_dropped": ("删除该段", "这段成分记录没有进库"),
    "row_added": ("新增该行", "这行数据只来自一个来源"),
    "duplicate_dropped": ("删除重复", "重复的那份已删除"),
    "resolved_baostock": ("按处理取 BaoStock", "按批准的处理保留 BaoStock 的值"),
    "resolved_seed": ("按处理取种子", "按批准的处理采用库里原来（种子）的值"),
    "resolved_null": ("按处理置空", "按批准的处理把成交额置为未知，K 线保留"),
    "resolved_no_bar": ("按处理不建 K 线", "按批准的处理不建这根 K 线，这天按停牌处理"),
    "resolved_corrected": ("按核实记录改正", "按批准的处理，用核实记录里的值改正"),
    "flagged": ("仅标记", "数据没有改动，只是标出来供查看"),
    "value_replaced": ("替换为新值", "库里的值换成了新来源的值"),
    "bar_kept": ("保留 K 线", "保留这根 K 线"),
    "no_bar": ("没有 K 线", "这天没有 K 线"),
    "status_dropped": ("删除状态行", "这行状态没有进库"),
    "event_dropped": ("删除事件", "这个除权事件没有进库"),
    "event_added": ("新增事件", "这个除权事件只来自 BaoStock"),
    "limits_voided": ("涨跌停置为未知", "这天的涨跌停按未知处理"),
}

METRICS = {
    "trading_days": ("交易日数", "回测覆盖的交易日个数"),
    "total_return": ("总收益", "期末权益相对期初的涨幅"),
    "annual_return": ("年化收益", "按每年交易日数折算的复合收益率"),
    "annual_volatility": ("年化波动", "日收益率标准差按年折算"),
    "sharpe": ("夏普比率", "超出无风险利率的收益除以波动，按年折算"),
    "annual_return_ex_best": ("去掉最好几天的年化收益", "去掉收益最高的一部分交易日后的年化收益，看收益是否靠少数几天"),
    "max_drawdown": ("最大回撤", "权益从高点回落的最大幅度"),
    "max_drawdown_peak": ("最大回撤起点", "最大回撤开始前的权益高点日期"),
    "max_drawdown_trough": ("最大回撤低点", "最大回撤的最低点日期"),
    "max_drawdown_recovery": ("最大回撤恢复日", "权益重新回到高点的日期"),
    "max_drawdown_days": ("最大回撤持续天数", "从高点到恢复所用的交易日数"),
    "max_days_without_new_high": ("最长未创新高天数", "权益连续没有创新高的最长交易日数"),
    "win_rate_daily": ("日胜率", "收益为正的交易日占比"),
    "win_rate_weekly": ("周胜率", "收益为正的周占比"),
    "win_rate_monthly": ("月胜率", "收益为正的月占比"),
    "excess_win_rate_daily": ("日超额胜率", "跑赢基准的交易日占比"),
    "excess_win_rate_weekly": ("周超额胜率", "跑赢基准的周占比"),
    "excess_win_rate_monthly": ("月超额胜率", "跑赢基准的月占比"),
    "benchmark_missing_days": ("基准缺失天数", "基准没有行情的交易日数"),
    "tracking_error": ("跟踪误差", "相对基准的超额收益的年化波动"),
    "information_ratio": ("信息比率", "年化超额收益除以跟踪误差"),
    "turnover": ("年化换手率", "每年买卖金额相对权益的倍数"),
    "avg_holding_days": ("平均持有天数", "每次持有一只股票的平均交易日数"),
    "executable_order_share": ("可成交订单占比", "调仓订单里成交（含部分成交）的比例"),
    "capacity_max": ("容量占比最大值", "单笔成交额占当天该股成交额的最大比例"),
    "capacity_p95": ("容量占比 95 分位", "单笔成交额占当天该股成交额比例的 95 分位"),
    "capacity_unknown_fills": ("容量未知的成交", "当天成交额未知、算不出容量占比的成交笔数"),
}

METRIC_REASONS = {
    "too_few_periods": ("周期太少", "交易日太少，算不出这个指标"),
    "zero_volatility": ("波动为零", "收益没有波动，比率无法计算"),
    "no_benchmark": ("没有基准", "这次回测没有设置基准"),
    "too_few_benchmark_days": ("基准天数太少", "基准有行情的交易日太少"),
    "no_periods": ("没有完整周期", "没有一个完整的周或月"),
    "no_drawdown": ("没有回撤", "权益从未回落"),
    "not_recovered": ("尚未恢复", "回测结束时还没有回到之前的高点"),
    "no_trade_values": ("没有成交金额", "没有成交，算不出换手率"),
    "no_positions": ("没有持仓", "从未持有股票"),
    "never_held": ("从未持有", "没有一次完整的持有"),
    "no_orders": ("没有订单", "回测中没有任何订单"),
    "no_rebalance_orders": ("没有调仓订单", "只有退市处理，没有调仓订单"),
    "no_order_amounts": ("没有订单金额", "订单没有成交金额"),
    "no_fills": ("没有成交", "没有任何成交"),
    "no_day_amounts": ("缺当天成交额", "成交的股票当天成交额未知"),
}

UNITS = {
    "fraction": ("比例", "0 到 1 之间的比例，显示为百分比"),
    "ratio": ("比率", "无单位的比值"),
    "days": ("天", "交易日数"),
    "date": ("日期", "一个日期"),
    "count": ("个数", "计数"),
}

FUNNEL_KINDS = {
    "listed": ("在市", "决策日已上市、未退市"),
    "bar": ("有 K 线", "决策日有 K 线（没有停牌、不缺数据）"),
    "universe": ("范围", "在策略的范围里：沪深全部或自定义名单，以及自定义的范围条件"),
    "pool": ("股票池", "股票池的一项：指数成分、板块、剔除 ST、上市天数"),
    "condition": ("条件", "一个筛选条件"),
    "group": ("条件组", "一组条件，全部或任一满足"),
    "scored": ("打分", "所有因子都有值、参与排名"),
    "targets": ("入选", "排名进入持仓数"),
    "backups": ("备选", "排名在入选之后的备选"),
}

ORDER_STATUSES = {
    "filled": ("全部成交", "按要求的股数全部成交"),
    "partial": ("部分成交", "现金不够，只成交了一部分"),
    "rejected": ("未成交", "订单被拒，原因见订单原因"),
}

ORDER_REASONS = {
    "no_position": ("没有持仓", "要卖的股票并没有持仓"),
    "no_bar": ("当天没有 K 线", "成交日停牌或缺数据，无法成交"),
    "limit_unknown": ("涨跌停未知", "当天涨跌停价未知，保守起见不成交"),
    "limit_up": ("涨停买不进", "开盘即涨停封板，买单无法成交"),
    "limit_down": ("跌停卖不出", "开盘即跌停封板，卖单无法成交"),
    "t1": ("T+1 限制", "当天买入的股票不能当天卖出"),
    "lot": ("不足一手", "按手数取整后不足一手"),
    "cash": ("现金不足", "现金不够买入一手"),
    "delist_write_down": ("退市减记", "退市日按规则把持仓减记"),
    "delist_exit": ("退市按收盘价清仓", "退市日按最后收盘价把持仓换成现金"),
}

SELL_KINDS = {
    "universe": ("调出范围", "不再满足策略的范围或股票池"),
    "filter": ("未通过筛选", "没有通过某个筛选条件"),
    "no_score": ("没有分数", "某个因子没有值，无法排名"),
    "rank": ("排名下降", "跌出持有范围"),
    "slots": ("名额已满", "排名靠前的股票占满了持仓名额"),
}

DELISTING_TREATMENTS = {
    "write_down": ("减记", "退市日把持仓按回收比例减记为现金（默认为零）"),
    "exit": ("按最后收盘价卖出", "退市日按最后收盘价把持仓换成现金"),
    "hold": ("继续持有", "退市后照旧持有，按最后收盘价估值"),
}

DELISTING_KINDS = {
    "forced": ("强制退市", "名称带“退”或最后收盘价低于 1 元"),
    "merger": ("吸收合并或私有化", "有官方退市日和名称，不是强制退市"),
    "suspension": ("长期停牌", "官方日期显示仍然上市"),
    "unknown": ("原因不明", "数据里看不出退市原因"),
}

ANOMALY_CLASSES = {
    "price": ("价格", "开高低收或昨收"),
    "volume": ("量额", "成交量或成交额"),
    "bar": ("整根 K 线", "整根 K 线只有一个来源"),
    "gap": ("缺数据", "应有 K 线的一天没有 K 线"),
    "limits": ("涨跌停", "当天的涨跌停价"),
    "status": ("交易状态", "停牌或交易的状态"),
    "adjustment": ("复权", "复权因子或除权事件"),
}

ANOMALY_LEVELS = {
    "error": ("错误", "和交易所数据矛盾，已经改正"),
    "suspicious": ("可疑", "内部有矛盾，没有外部核实"),
    "notice": ("提示", "口径不同，或者数据其实没错"),
}

ERROR_CODES = {
    "admits_beijing": ("包含北交所", "范围会包含北交所股票，需要显式写明"),
    "bad_arity": ("参数个数不对", "函数的参数个数不对"),
    "bad_constant": ("常数不合法", "常数不是有限的数"),
    "bad_file": ("文件无法导入", "这个文件不是 WeQuant 导出的回测、数据版本或策略文件，或者内容被改过"),
    "bad_ref": ("引用无效", "引用的名称或编号不存在"),
    "descriptive_field": ("不能用描述字段", "这个字段只用来展示，不能用在信号里，否则会用到未来信息"),
    "duplicate_name": ("名称重复", "同一层里的名称重复了"),
    "duplicate_value": ("取值重复", "列表里有重复的值"),
    "file_in_use": ("文件被占用", "有文件正被别的程序打开，这次没有做任何改动；关掉那个程序后再试"),
    "future_function": ("用到未来数据", "这个写法会读到决策日之后的数据"),
    "hash_mismatch": ("文件被改动", "保存后的策略版本文件被手工改过"),
    "job_unfinished": ("任务还没完成", "任务完成后才有结果"),
    "missing_key": ("缺少字段", "缺少必填字段"),
    "needs_condition": ("需要条件表达式", "这里需要一个真或假的条件"),
    "needs_integer": ("需要整数", "这里需要一个整数"),
    "needs_series": ("需要序列", "这里需要一个随日期变化的序列"),
    "negative_window": ("窗口不能为负", "窗口长度不能是负数"),
    "no_data": ("没有数据", "所选范围内没有数据"),
    "no_job_queue": ("没有任务队列", "后台任务只在 wequant serve 里运行"),
    "no_resolutions": ("没有裁决文件", "数据目录里没有裁决文件"),
    "no_route": ("没有这个接口", "这个地址上没有操作，操作是 POST /api/<操作名>"),
    "no_file": ("没有这个文件", "要导入的文件不存在"),
    "no_stock_basic": ("没有股票基础信息", "还没有从 BaoStock 取过股票基础信息（stock_basic）"),
    "source_unreachable": ("连不上 BaoStock", "BaoStock 无法访问或登录失败，数据库没有改动"),
    "store_exists": ("已经有数据", "这个数据目录里已经有数据：请换一个空的数据目录再导入"),
    "store_busy": ("数据库正在被占用", "另一个写入或 BaoStock 任务正在进行，请稍后再试；数据库没有改动"),
    "no_store": ("没有数据库", "数据目录里还没有数据库"),
    "no_directory": ("没有这个文件夹", "这个文件夹不存在"),
    "no_access": ("没有权限", "没有权限打开或写入这个文件夹"),
    "bad_name": ("名称不能用", "文件夹名称为空，或者含有不能用的字符"),
    "exists": ("已经存在", "同名的文件夹已经存在"),
    "not_a_data_root": ("不是数据目录", "这个文件夹里没有 WeQuant 数据"),
    "data_root_incomplete": ("数据不完整", "数据版本记录不完整，缺少文件或者读不出来"),
    "not_empty": ("文件夹不是空的", "新位置要是一个空文件夹，或者还不存在的文件夹"),
    "inside": ("位置互相包含", "新位置不能在当前数据目录里面，当前数据目录也不能在新位置里面"),
    "no_space": ("空间不够", "新位置所在的磁盘可用空间不够放下这些数据"),
    "copy_mismatch": ("复制结果不一致", "复制后的数据和原来的对不上，已经清理掉复制的部分，原数据没有变"),
    "not_a_choice": ("不在可选值里", "取值不在允许的选项里"),
    "not_a_decision_day": ("不是决策日", "这一天不是这次运行的决策日或成交日"),
    "not_a_form": ("不是表达式", "不是合法的表达式写法"),
    "not_a_trading_day": ("不是交易日", "这一天不是交易日"),
    "not_available": ("暂不可用", "这个指标暂时还没有数据"),
    "not_json": ("不是 JSON", "内容不是合法的 JSON"),
    "out_of_range": ("超出范围", "数值超出允许的范围"),
    "outside_data_root": (
        "数据目录指向外部",
        "数据目录里有文件夹指向数据目录之外，为避免改动其他位置的数据，拒绝写入和清理；只读操作照常可用",
    ),
    "requires": ("缺少前提", "这个设置需要另一个设置一起出现"),
    "reserved_name": ("保留名称", "这个名称是保留字，不能用"),
    "too_deep": ("嵌套太深", "表达式嵌套层数太多"),
    "too_large": ("表达式太大", "表达式的节点太多"),
    "undetermined": ("无法批准", "这条处理需要外部证据，不能直接批准"),
    "unknown_code": ("没有这只股票", "数据里没有这个代码"),
    "unknown_field": ("没有这个字段", "表达式里用了不存在的字段"),
    "unknown_job": ("没有这个任务", "没有这个任务，或者它已经被清理"),
    "unknown_pool": ("没有这个股票池", "没有这个股票池，可能已经删除"),
    "unknown_key": ("多余的字段", "出现了不认识的字段"),
    "unknown_operator": ("没有这个函数", "表达式里用了不存在的函数"),
    "unknown_run": ("没有这次运行", "运行记录里没有这个编号"),
    "unknown_strategy": ("没有这个策略", "没有保存过这个策略"),
    "unknown_version": ("没有这个版本", "这个策略没有这个版本"),
    "version_not_kept": ("数据版本已清理", "这个数据版本已经不在了"),
    "wrong_type": ("类型不对", "取值的类型不对"),
}

EXITS = {
    "OK": ("成功", "操作成功"),
    "PROBLEMS": ("发现问题", "检查完成并发现了需要处理的问题"),
    "USAGE": ("请求有误", "参数或输入不合法"),
    "NOT_FOUND": ("找不到", "没有这个数据库、版本、代码、交易日、策略或运行"),
    "UNREACHABLE": ("连不上数据源", "数据源无法访问"),
    "LOCKED": ("正在被占用", "另一个写入正在进行，请稍后再试"),
    "INTERNAL": ("程序错误", "程序内部出错"),
    "INTERRUPTED": ("已中断", "按了 Ctrl-C，或任务被取消"),
}

VERSION_REASONS = {  # code -> (name, template over the reason's params)
    "seed_import": ("导入种子数据", "导入种子数据"),
    "baostock_merge": ("合并 BaoStock", "合并 BaoStock 的 {days} 个交易日，应用 {resolutions} 条已批准的数据问题处理"),
    "limit_check": ("提交涨跌停检查", "提交涨跌停检查"),
    "daily_update": ("每日更新", "每日更新，补了 {days} 个交易日"),
    "first_build": ("首次建库", "首次建库，从 BaoStock 取了 {days} 个交易日"),
    "history_backfill": ("补齐历史", "补齐历史 {first_year}–{last_year}，{days} 个交易日"),
    "own_import": ("导入自备数据", "导入自备数据，{days} 个交易日"),
    "names": ("补充股票名称", "补充股票名称"),
    "version_import": ("从文件导入", "从文件导入（原版本 {name}）"),
    "demo": ("生成演示数据", "生成演示数据"),
    "unspecified": ("未注明原因", "未注明原因"),
    "legacy": ("早期版本", "早期版本"),
}

ORDER_CAUSES = {  # code -> (name, template over the order's cause_params)
    "enter": ("新进买入", "新进入持有名单买入（排第 {rank} 名）"),
    "backup": ("备选递补", "备选递补买入（排第 {rank} 名）"),
    "rebalance": ("权重调整", "权重偏离目标 {drift:+.1%}，调整到 {weight:.1%}"),
    "exit_pool": ("调出股票池", "调出股票池卖出（{step}）"),
    "exit_filter": ("不再满足条件", "不再满足条件卖出（{step}）"),
    "exit_no_score": ("缺少因子值", "缺少因子值卖出（{step}）"),
    "exit_rank": ("跌出持有范围", "跌出持有范围卖出（排第 {rank} 名，持有前 {limit} 名）"),
    "exit_slots": ("名额已满", "持有名额已满，被排名更前的股票替换卖出（排第 {rank} 名，名额 {limit} 个）"),
    "delisting": ("退市处理", "按退市规则处理"),
    "unrecorded": ("未记录", "这笔订单没有可对应的决策记录，原因未记录"),
}

DAY_SOURCES = {
    "stored": ("交易日", "本地数据里的交易日"),
    "calendar": ("交易日历", "BaoStock 已公布的交易日"),
    "estimated": ("估算", "按工作日估算，节假日可能调整"),
}

JOB_KINDS = {
    "backtest": ("回测", "按策略回测一段区间，结果存进运行记录"),
    "data_update": ("每日更新", "从 BaoStock 补齐本地数据之后已收盘的交易日"),
    "data_relocate": ("更换数据目录", "把数据移动或复制到新的文件夹，检查无误后改用它"),
    "data_build": ("首次建库", "从 BaoStock 建好最近几年的数据"),
    "data_backfill": ("补齐历史", "在后台把更早的历史一段一段补进来"),
    "data_own_import": ("导入自备数据", "把自己的数据文件导入成一个数据版本"),
}

JOB_STATES = {
    "queued": ("排队中", "等前面的任务结束后开始"),
    "running": ("进行中", "任务正在执行，可以取消"),
    "succeeded": ("已完成", "任务完成，可以查看结果"),
    "failed": ("失败", "任务没有完成，原因见说明"),
    "cancelled": ("已取消", "任务被取消，没有留下结果"),
}

JOB_STAGES = {  # code -> (name, template over the stage's params)
    "queued": ("排队中", "排队中，排在第 {position} 位"),
    "start": ("启动中", "正在启动"),
    "load": ("读取数据", "正在读取数据"),
    "backtest": ("回测中", "正在回测 {day}：第 {done}/{total} 个交易日，总资产 {equity:,.2f} 元"),
    "cancelling": ("取消中", "正在取消，没做完的部分不会保存"),
    "done": ("已完成", "已完成"),
    "failed": ("失败", "失败：{reason}"),
    "cancelled": ("已取消", "已取消，没有留下结果"),
    "crashed": ("进程退出", "任务进程意外退出（退出码 {exit_code}）"),
    "interrupted": ("服务停止", "wequant serve 停止时任务还没有完成"),
    "update_calendar": ("读取交易日历", "正在读取交易日历"),
    "update_snapshot": ("下载行情", "正在下载 {day} 的行情（已完成 {done}/{total} 天）"),
    "update_factors": ("下载复权因子", "正在下载复权因子（已完成 {done}/{total} 只）"),
    "update_indexes": ("下载指数", "正在下载指数行情"),
    "update_merge": ("检查合并", "正在检查和合并新数据"),
    "update_commit": ("写入版本", "正在写入新版本"),
    "build_calendar": ("读取交易日历", "正在读取交易日历"),
    "build_stocks": ("下载行情", "正在下载 {code} 的行情（已完成 {done}/{total} 只）"),
    "build_factors": ("下载复权因子", "正在下载复权因子（已完成 {done}/{total} 只）"),
    "build_indexes": ("下载指数", "正在下载沪深300和中证500的行情和成分"),
    "build_merge": ("检查合并", "正在检查数据、整理成表"),
    "build_commit": ("写入版本", "正在写入数据版本"),
    "build_reconnect": (
        "等待重连",
        "BaoStock 连接中断，{seconds} 秒后重新连接（第 {attempt}/{of} 次），已下载的部分不会重下",
    ),
    "own_read": ("读取并检查文件", "正在读取并检查自备数据"),
    "own_commit": ("写入版本", "正在把自备数据写成数据版本"),
    "relocate_check": ("检查目标位置", "正在检查目标文件夹和可用空间"),
    "relocate_copy": ("复制数据", "正在复制数据（已复制 {done_mb}/{total_mb} MB）"),
    "relocate_switch": ("切换目录", "复制完成并核对无误，正在改用新的数据目录"),
    "relocate_remove": ("删除旧目录", "正在删除旧位置的数据"),
}

SCOPES = {  # code -> (name, sentence, kind): the scopes a new strategy offers, in this order
    "all": ("沪深全部", "沪深两市的全部股票", "all"),
    "000300.SH": ("沪深300", "沪深300指数在决策日的成分股", "index"),
    "000905.SH": ("中证500", "中证500指数在决策日的成分股", "index"),
    "stocks": ("自定义名单", "从自己的股票池复制的一份固定名单", "stocks"),
}

LEGACY_SCOPES = {  # code -> (name, sentence): older index options existing strategies still read
    "000906.SH": ("中证800", "中证800指数在决策日的成分股；旧选项，新建策略不再提供"),
    "000852.SH": ("中证1000", "中证1000指数在决策日的成分股；旧选项，新建策略不再提供"),
    "000985.SH": ("中证全指", "中证全指在决策日的成分股；旧选项，新建策略不再提供"),
}

OWN_RULES = {  # the rules the user's own files are checked against
    "missing_columns": ("缺少必需的列", "没有日线文件，或者缺少代码、日期、开高低收、成交量、成交额中的某一列"),
    "unreadable": ("读不出来的行", "代码、日期或数字读不出来，这些行不会导入"),
    "duplicate": ("重复的行", "和前面一行的代码、日期相同，只导入前一行"),
    "price_order": (
        "价格不合理",
        "开高低收对不上（有价格不大于 0，或最高价低于其他价格、最低价高于其他价格），按原样导入并标记",
    ),
    "no_list_date": ("缺上市日期", "这些股票没有上市日期，上市天数会从它们的第一根 K 线算起"),
}

BUILD_STATUSES = {
    "built": ("已建好", "已经建好一个新的数据版本"),
    "exists": ("已经有数据", "这个数据目录里已经有数据，没有新建版本"),
    "complete": ("历史已补齐", "更早的历史已经补齐，没有可以再补的了"),
    "not_published": ("数据未发布", "BaoStock 还没有这段时间的数据，请稍后再试"),
}

INDICATOR_CATEGORIES = {
    "price": ("价格", "当天的开盘、最高、最低、收盘价，不复权"),
    "adjusted_price": ("复权价格", "按后复权调整过的价格和复权因子"),
    "volume": ("成交", "成交量、成交额和换手率"),
    "momentum": ("动量", "一段时间的涨跌幅"),
    "volatility": ("波动", "涨跌的剧烈程度"),
    "trend": ("趋势", "和均线、近期高点的距离"),
    "limit": ("涨跌停", "涨跌停价和是否涨跌停"),
    "status": ("状态", "ST 等交易状态"),
    "classification": ("板块与交易所", "所在的板块和交易所"),
    "membership": ("指数成分", "是不是某个指数的成分股"),
    "listing": ("上市", "上市了多久"),
}

PARSE_REASONS = {
    "not_found": ("找不到", "数据里没有这个代码或名称的股票"),
    "ambiguous": ("有歧义", "这个名称或代码对应不止一只股票，请改用带交易所的代码，例如 600000.SH"),
    "beijing": ("北交所暂不支持", "北交所的股票暂时没有数据"),
    "not_a_stock": ("不是股票", "这是指数或基金的代码，不是股票"),
    "header": ("表头", "像是表头（例如“代码”“名称”），已跳过"),
}

UPDATE_STATUSES = {  # code -> (name, template over update_params)
    "updated": ("已更新", "更新完成：新增 {days} 个交易日，数据到 {last_day}"),
    "up_to_date": ("已是最新", "已是最新，没有新的已收盘交易日"),
    "not_published": ("数据未发布", "{waiting} 的数据还没有发布，请收盘几小时后再试"),
    "conflicts": ("等待处理", "有 {unresolved} 个冲突需要先在数据页处理（新提议 {proposed} 条），这次没有更新"),
    "rejected": ("数据未通过检查", "BaoStock 的数据没有通过检查，这次没有更新"),
}

# ---------------------------------------------------------------- models and the operation


class LabelsRequest(Model):
    """Every label table."""


class Label(Model):
    """One code's Chinese name and sentence."""

    code: str
    label: str
    """Short, for tables and chips."""
    help: str
    """One sentence, for tooltips."""


class IssueKindLabel(Label):
    """A data_issues kind, with what schema.py says of it."""

    table: str
    state: bool
    """True: every merge or check recomputes it (it describes the data as it is); false: a record of one action."""
    anomaly: str | None
    """Its anomaly class; null when it marks no cell a run reads."""
    level: str | None


class TemplateLabel(Model):
    """A code whose sentence is filled from params: {name}, {name:.1%} (a fraction as a percent) or {name:,.2f}."""

    code: str
    label: str
    template: str
    params: list[str]
    """The keys template uses."""


class WarningLabel(TemplateLabel):
    """A warning: template, plus optional clauses and repetition (fill shows how)."""

    optional: dict[str, str]
    """param -> clause appended when that param is not null."""
    repeat: str | None
    """Not null: params repeat per item with suffixes _2, _3, ...; the filled templates are joined by this."""


class ScopeLabel(Label):
    """A scope a strategy may have: all stocks, an index's members, or a fixed list of stocks."""

    kind: Literal["all", "index", "stocks"]
    legacy: bool
    """True: an older index option, still read in existing strategies, never offered for new ones."""


class Labels(Model):
    """The Chinese of every code: warnings, data issues, metrics, funnel, orders, delistings, anomalies, errors,
    versions and jobs."""

    warnings: list[WarningLabel]
    issue_kinds: list[IssueKindLabel]
    issue_actions: list[Label]
    metrics: list[Label]
    metric_reasons: list[Label]
    units: list[Label]
    funnel_kinds: list[Label]
    order_statuses: list[Label]
    order_reasons: list[Label]
    sell_kinds: list[Label]
    delisting_treatments: list[Label]
    delisting_kinds: list[Label]
    anomaly_classes: list[Label]
    anomaly_levels: list[Label]
    error_codes: list[Label]
    exits: list[Label]
    version_reasons: list[TemplateLabel]
    job_kinds: list[Label]
    job_states: list[Label]
    job_stages: list[TemplateLabel]
    update_statuses: list[TemplateLabel]
    """data update's statuses; params: days (merged), last_day, waiting, proposed, unresolved."""
    order_causes: list[TemplateLabel]
    """Why an order was placed (OrderRow.cause), filled from its cause_params."""
    day_sources: list[Label]
    """Where a scheduled rebalance day comes from (ScheduledDay.source)."""
    scopes: list[ScopeLabel]
    """The scopes a strategy may have, the offered ones first in the order to show; legacy ones last."""
    parse_reasons: list[Label]
    """Why an item of a pasted list or file names no stock (UnparsedItem.reason)."""
    indicator_categories: list[Label]
    """The categories of expr indicators (Indicator.category)."""
    own_rules: list[Label]
    """The rules the user's own files are checked against (OwnProblem.rule)."""
    build_statuses: list[Label]
    """What a first build or a backfill segment did (BuildResult.status)."""


def labels(request: LabelsRequest, context: Context = Context()) -> Labels:
    """Every label table; the same for the same code."""
    return Labels(
        warnings=[_warning(code, *entry) for code, entry in WARNINGS.items()],
        issue_kinds=[_issue_kind(code, *text) for code, text in ISSUE_KIND_TEXT.items()],
        issue_actions=_labels(ISSUE_ACTION_TEXT),
        metrics=_labels(METRICS),
        metric_reasons=_labels(METRIC_REASONS),
        units=_labels(UNITS),
        funnel_kinds=_labels(FUNNEL_KINDS),
        order_statuses=_labels(ORDER_STATUSES),
        order_reasons=_labels(ORDER_REASONS),
        sell_kinds=_labels(SELL_KINDS),
        delisting_treatments=_labels(DELISTING_TREATMENTS),
        delisting_kinds=_labels(DELISTING_KINDS),
        anomaly_classes=_labels(ANOMALY_CLASSES),
        anomaly_levels=_labels(ANOMALY_LEVELS),
        error_codes=_labels(ERROR_CODES),
        exits=_labels(EXITS),
        version_reasons=[_template(code, name, text) for code, (name, text) in VERSION_REASONS.items()],
        job_kinds=_labels(JOB_KINDS),
        job_states=_labels(JOB_STATES),
        job_stages=[_template(code, name, text) for code, (name, text) in JOB_STAGES.items()],
        update_statuses=[_template(code, name, text) for code, (name, text) in UPDATE_STATUSES.items()],
        order_causes=[_template(code, name, text) for code, (name, text) in ORDER_CAUSES.items()],
        day_sources=_labels(DAY_SOURCES),
        scopes=[ScopeLabel(code=c, label=n, help=h, kind=k, legacy=False) for c, (n, h, k) in SCOPES.items()]
        + [ScopeLabel(code=c, label=n, help=h, kind="index", legacy=True) for c, (n, h) in LEGACY_SCOPES.items()],
        parse_reasons=_labels(PARSE_REASONS),
        indicator_categories=_labels(INDICATOR_CATEGORIES),
        own_rules=_labels(OWN_RULES),
        build_statuses=_labels(BUILD_STATUSES),
    )


def fill(label: WarningLabel | TemplateLabel, params: Mapping) -> str:
    """The Chinese sentence of label with params, as the frontend renders it: optional clauses only for set params,
    repeated items joined."""
    clauses = getattr(label, "optional", {})

    def item(suffix: str) -> str:
        values = {p: params.get(p + suffix) for p in label.params}
        return (label.template + "".join(c for p, c in clauses.items() if values[p] is not None)).format(**values)

    repeat = getattr(label, "repeat", None)
    if repeat is None:
        return item("")
    lead = label.params[0]
    numbers = sorted(int(k[len(lead) + 1 :]) for k in params if re.fullmatch(rf"{lead}_[0-9]+", k))
    return repeat.join(item(suffix) for suffix in ["", *(f"_{n}" for n in numbers)])


def reason_text(reason: Mapping) -> str:
    """The Chinese sentence of a version's reason {code, params}; its name when params lack a key."""
    name, template = VERSION_REASONS[reason["code"]]
    try:
        return template.format(**reason.get("params", {}))
    except (KeyError, IndexError):
        return name


def job_text(stage: str, params: Mapping) -> str:
    """The Chinese sentence of a job's stage with its params."""
    return JOB_STAGES[stage][1].format(**params)


def update_params(result: Mapping) -> dict:
    """The params of a data update's status sentence, from the result's fields."""
    days = result["days"]
    return {
        "days": len(days),
        "last_day": days[-1].isoformat() if days else None,
        "waiting": result["waiting"] and result["waiting"].isoformat(),
        "proposed": result["proposed"],
        "unresolved": result["unresolved"],
    }


def update_text(result: Mapping) -> str:
    """The Chinese sentence of a data update's status."""
    return UPDATE_STATUSES[result["status"]][1].format(**update_params(result))


def placeholders(template: str) -> list[str]:
    """The parameter names in template, in order, once each."""
    found = [field for _, field, _, _ in string.Formatter().parse(template) if field]
    return list(dict.fromkeys(found))


def _warning(code: str, name: str, template: str, optional: dict, repeat: str | None) -> WarningLabel:
    keys = placeholders(template + "".join(optional.values()))
    return WarningLabel(code=code, label=name, template=template, params=keys, optional=optional, repeat=repeat)


def _template(code: str, name: str, template: str) -> TemplateLabel:
    return TemplateLabel(code=code, label=name, template=template, params=placeholders(template))


def _issue_kind(code: str, name: str, help: str) -> IssueKindLabel:
    kind = ISSUE_KINDS[code]
    fields = {"table": kind.table, "state": kind.state, "anomaly": kind.anomaly, "level": kind.level}
    return IssueKindLabel(code=code, label=name, help=help, **fields)


def _labels(table: Mapping[str, tuple[str, str]]) -> list[Label]:
    return [Label(code=code, label=name, help=help) for code, (name, help) in table.items()]
