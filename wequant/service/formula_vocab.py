"""formula vocab (S4.1): every function and field a formula can use, in Chinese, the TDX names it refuses and why, and
examples, for the formula editor's completion and help."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from wequant.formula.names import (
    DRAWING,
    FAMILY_FUNCTIONS,
    FIELD_NAMES,
    FUTURE_NAMES,
    NO_DATA,
    PERIOD_FUNCTIONS,
    UNSUPPORTED,
)
from wequant.service.base import Context, Model
from wequant.service.hints import MISSING

Category = Literal[
    "price", "volume", "status", "membership", "reference", "average", "statistics", "logic", "math",
    "cross_section", "period",
]  # fmt: skip
Returns = Literal["num", "bool", "same"]
Role = Literal["condition", "factor", "universe", "formula", "sell", "keep"]

FUNCTION_TEXT: dict[
    str, tuple[Category, str, Returns, str, str]
] = {  # name -> (category, args, returns, text, example)
    "REF": ("reference", "X, N", "same", "N 根 K 线之前的 X", "REF(CLOSE, 1)"),
    "BARSLAST": (
        "reference",
        "COND",
        "num",
        "上一次条件成立到现在隔了几根 K 线",
        "BARSLAST(CROSS(MA(CLOSE,5), MA(CLOSE,20)))",
    ),
    "BARSCOUNT": ("reference", "X", "num", "从数据开始到现在 X 有值的 K 线根数", "BARSCOUNT(CLOSE)"),
    "VALUEWHEN": (
        "reference",
        "COND, X",
        "same",
        "最近一次条件成立那根 K 线上 X 的值",
        "VALUEWHEN(CROSS(CLOSE, MA(CLOSE,20)), CLOSE)",
    ),
    "MA": ("average", "X, N", "num", "最近 N 根的简单平均；不够 N 根时为空", "MA(CLOSE, 20)"),
    "EMA": ("average", "X, N", "num", "N 根的指数平均（权重 2/(N+1)），从数据开始算起", "EMA(CLOSE, 12)"),
    "SMA": ("average", "X, N, M", "num", "通达信 SMA：Y = (M×X + (N−M)×Y′) / N，从数据开始算起", "SMA(CLOSE, 3, 1)"),
    "WMA": ("average", "X, N", "num", "最近 N 根的加权平均，越近权重越大", "WMA(CLOSE, 10)"),
    "SUM": ("statistics", "X, N", "num", "最近 N 根的合计；N 为 0 时从数据开始累计", "SUM(VOL, 5)"),
    "HHV": (
        "statistics",
        "X, N",
        "num",
        "最近 N 根里的最高值；不够 N 根时为空（通达信用已有的几根算，只差在上市后头几根）",
        "HHV(HIGH, 20)",
    ),
    "LLV": (
        "statistics",
        "X, N",
        "num",
        "最近 N 根里的最低值；不够 N 根时为空（通达信用已有的几根算，只差在上市后头几根）",
        "LLV(LOW, 20)",
    ),
    "HHVBARS": ("statistics", "X, N", "num", "最近 N 根里最高值到现在隔了几根", "HHVBARS(HIGH, 20)"),
    "LLVBARS": ("statistics", "X, N", "num", "最近 N 根里最低值到现在隔了几根", "LLVBARS(LOW, 20)"),
    "STD": ("statistics", "X, N", "num", "最近 N 根的样本标准差", "STD(CLOSE, 20)"),
    "STDP": ("statistics", "X, N", "num", "最近 N 根的总体标准差", "STDP(CLOSE, 20)"),
    "AVEDEV": ("statistics", "X, N", "num", "最近 N 根的平均绝对偏差", "AVEDEV(CLOSE, 14)"),
    "SLOPE": ("statistics", "X, N", "num", "最近 N 根的线性回归斜率", "SLOPE(CLOSE, 20)"),
    "TS_RANK": ("statistics", "X, N", "num", "最新一根在最近 N 根里的排名，最高为 1（通达信没有）", "TS_RANK(VOL, 20)"),
    "CORR": ("statistics", "A, B, N", "num", "A 和 B 最近 N 根的相关系数（通达信没有）", "CORR(CLOSE, VOL, 20)"),
    "COUNT": ("logic", "COND, N", "num", "最近 N 根里条件成立的次数；N 为 0 时从数据开始数", "COUNT(CLOSE > OPEN, 5)"),
    "EVERY": ("logic", "COND, N", "bool", "最近 N 根条件都成立", "EVERY(CLOSE > MA(CLOSE,20), 3)"),
    "EXIST": ("logic", "COND, N", "bool", "最近 N 根里条件至少成立一次", "EXIST(CLOSE > REF(CLOSE,1)*1.09, 10)"),
    "CROSS": ("logic", "A, B", "bool", "A 上穿 B：这一根 A > B，上一根 A ≤ B", "CROSS(MA(CLOSE,5), MA(CLOSE,20))"),
    "IF": ("logic", "COND, A, B", "same", "条件成立取 A，否则取 B", "IF(CLOSE > OPEN, 1, -1)"),
    "BETWEEN": ("logic", "X, A, B", "bool", "X 在 A 和 B 之间（含两端）", "BETWEEN(CLOSE / MA(CLOSE,20), 0.95, 1.05)"),
    "NOT": ("logic", "COND", "bool", "条件不成立", "NOT(CLOSE > OPEN)"),
    "MAX": ("math", "A, B", "num", "A 和 B 中较大的", "MAX(OPEN, CLOSE)"),
    "MIN": ("math", "A, B", "num", "A 和 B 中较小的", "MIN(OPEN, CLOSE)"),
    "ABS": ("math", "X", "num", "绝对值", "ABS(CLOSE - OPEN)"),
    "LN": ("math", "X", "num", "自然对数", "LN(CLOSE)"),
    "CS_RANK": (
        "cross_section",
        "X",
        "num",
        "当天在股票池里的排名，最高为 1（通达信没有）",
        "CS_RANK(CLOSE / REF(CLOSE,20))",
    ),
    "CS_PCT": ("cross_section", "X", "num", "当天在股票池里的百分位（通达信没有）", "CS_PCT(AMOUNT)"),
    "CS_ZSCORE": (
        "cross_section",
        "X",
        "num",
        "当天在股票池里的标准分（通达信没有）",
        "CS_ZSCORE(CLOSE / REF(CLOSE,20))",
    ),
    "CS_DEMEAN": (
        "cross_section",
        "X",
        "num",
        "减去当天股票池的平均值（通达信没有）",
        "CS_DEMEAN(CLOSE / REF(CLOSE,20))",
    ),
    "WEEK": (
        "period",
        "X",
        "same",
        "在周线上算 X，周中沿用上一周的值（通达信没有；整条公式在周线上算时直接选周期）",
        "WEEK(CLOSE > MA(CLOSE,10))",
    ),
    "MONTH": ("period", "X", "same", "在月线上算 X，月中沿用上个月的值（通达信没有）", "MONTH(CLOSE > MA(CLOSE,12))"),
    "MEMBER": ("membership", "'代码'", "bool", "当天是这个指数的成分股（通达信没有）", "MEMBER('000300.SH')"),
    "BOARD": (
        "membership",
        "'板块'",
        "bool",
        "在这个板块：main 主板、chinext 创业板、star 科创板（通达信没有）",
        "BOARD('chinext')",
    ),
    "EXCHANGE": ("membership", "'交易所'", "bool", "在这个交易所：SH、SZ、BJ（通达信没有）", "NOT EXCHANGE('BJ')"),
    "CODES": ("membership", "'代码', …", "bool", "在这份名单里（通达信没有）", "CODES('600000.SH', '000001.SZ')"),
}
ALIASES = {"EXPMA": "EMA", "IFF": "IF"}
FIELD_TEXT: dict[str, tuple[str, Category, str]] = {  # text name -> (unit, category, sentence)
    "OPEN": ("yuan", "price", "开盘价，后复权"),
    "HIGH": ("yuan", "price", "最高价，后复权"),
    "LOW": ("yuan", "price", "最低价，后复权"),
    "CLOSE": ("yuan", "price", "收盘价，后复权：均线、涨幅、KDJ 这类指标用它，和通达信前复权算出的一样"),
    "PRECLOSE": ("yuan", "price", "前收盘价，后复权"),
    "RAW_OPEN": ("yuan", "price", "开盘价，不复权"),
    "RAW_HIGH": ("yuan", "price", "最高价，不复权"),
    "RAW_LOW": ("yuan", "price", "最低价，不复权"),
    "RAW_CLOSE": ("yuan", "price", "收盘价，不复权：和固定价位比较（例如低于 5 元）用它"),
    "RAW_PRECLOSE": ("yuan", "price", "交易所公布的前收盘价，不复权"),
    "VOL": ("lots", "volume", "成交量，单位手（100 股），和通达信一致"),
    "RAW_VOLUME": ("shares", "volume", "成交量，单位股"),
    "AMOUNT": ("yuan", "volume", "成交额，单位元；个别日子可能没有"),
    "FACTOR": ("ratio", "price", "后复权因子：后复权价等于不复权价乘以它"),
    "LIMIT_UP_PRICE": ("yuan", "status", "当天的涨停价"),
    "LIMIT_DOWN_PRICE": ("yuan", "status", "当天的跌停价"),
    "LIMIT_UP": ("bool", "status", "收盘涨停"),
    "LIMIT_DOWN": ("bool", "status", "收盘跌停"),
    "LIMIT_LOCKED": ("bool", "status", "一字板：最高等于最低，并且收在涨停或跌停"),
    "LIMIT_KNOWN": ("bool", "status", "当天的涨跌幅规则已知"),
    "ST": ("bool", "status", "当天是 ST"),
    "ST_KNOWN": ("bool", "status", "当天是不是 ST 有记录"),
    "LIST_DAYS": ("trading_days", "status", "上市以来的交易日数，当天也算"),
    "DELISTING": ("bool", "status", "处于退市整理期"),
}
EXAMPLES: list[tuple[str, str, str, str, Role, bool]] = [  # (title, description, text, period, role, complete)
    (
        "收盘价站上 20 日均线",
        "今天的收盘价高于最近 20 天的平均收盘价",
        "CLOSE > MA(CLOSE, 20)",
        "day",
        "condition",
        False,
    ),
    ("绝对价位", "不复权收盘价高于 5 元：比较固定价位要用不复权价", "RAW_CLOSE > 5", "day", "condition", False),
    ("20 日涨幅", "最近 20 个交易日的涨幅，越大排名越前", "C / REF(C, 20) - 1", "day", "factor", False),
    ("按 20 日平均成交额", "成交越活跃的股票权重越大", "MA(AMOUNT, 20)", "day", "formula", False),
    ("不含北交所", "沪深两市全部股票", "NOT EXCHANGE('BJ')", "day", "universe", False),
    (
        "J 下穿 K",
        "周线 KDJ 的 J 从上往下穿过 K 时卖出",
        "N:=9; M1:=3; M2:=3;\nRSV:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;\nK:=SMA(RSV,M1,1);\nD:=SMA(K,M2,1);\n"
        "J:=3*K-2*D;\nXG: CROSS(K,J);",
        "week",
        "sell",
        False,
    ),
    (
        "周线 KDJ 形态",
        "本周 J>K>D，上周 J、K、D 两两相差不到 10%，上上周 J>K>D：一条完整的周线公式，可以原样贴进来",
        "N:=9;M1:=3;M2:=3;\nRSV:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;\nK:=SMA(RSV,M1,1);\nD:=SMA(K,M2,1);\n"
        "J:=3*K-2*D;\n{本周 J>K>D}\nA:= J>K AND K>D;\n{上周（前1根周K线）}\nJ1:=REF(J,1);\nK1:=REF(K,1);\nD1:=REF(D,1);\n"
        "{两两相对差值≤10%，分母>0防除零}\nCOND_JK:= ABS(J1-K1)/MAX(J1,K1)<=0.10 AND MAX(J1,K1)>0;\n"
        "COND_KD:= ABS(K1-D1)/MAX(K1,D1)<=0.10 AND MAX(K1,D1)>0;\nCOND_JD:= ABS(J1-D1)/MAX(J1,D1)<=0.10 AND MAX(J1,D1)>0;\n"
        "B:= COND_JK AND COND_KD AND COND_JD;\n{上上周（前2根周K线）J>K>D}\nC:= REF(J,2)>REF(K,2) AND REF(K,2)>REF(D,2);\n"
        "XG: A AND B AND C;",
        "week",
        "condition",
        True,
    ),
]


class VocabRequest(Model):
    """No parameters."""


class FormulaFunction(Model):
    name: str
    aliases: list[str]
    args: str
    """The arguments as written, e.g. "X, N"."""
    returns: Returns
    """num, bool, or same: the kind of its value argument."""
    category: Category
    """labels.formula_categories."""
    description: str
    example: str
    tdx: bool
    """TDX has it under this name."""


class FormulaField(Model):
    name: str
    aliases: list[str]
    form: JsonValue
    """The tree it reads."""
    unit: Literal["yuan", "lots", "shares", "ratio", "bool", "trading_days"]
    category: Category
    description: str


class RefusedName(Model):
    """A TDX name a formula cannot use, and the error code it gets."""

    name: str
    code: Literal["future_function", "formula_no_data", "formula_unsupported"]
    reason: str


class FormulaExample(Model):
    title: str
    description: str
    text: str
    period: Literal["day", "week", "month"]
    role: Role
    """What it is an example of; a role's first example that is not complete is the default text for it."""
    complete: bool
    """A whole formula to show in the help, not a default."""


class Vocab(Model):
    functions: list[FormulaFunction]
    fields: list[FormulaField]
    unsupported: list[RefusedName]
    examples: list[FormulaExample]


def formula_vocab(request: VocabRequest, context: Context = Context()) -> Vocab:
    """Every function and field a formula can use, the names it refuses, and examples by role."""
    functions = [
        FormulaFunction(
            name=name, aliases=sorted(a for a, n in ALIASES.items() if n == name), args=args, returns=returns,
            category=category, description=text, example=example,
            tdx=name not in (*PERIOD_FUNCTIONS, *FAMILY_FUNCTIONS, "CODES", "TS_RANK", "CORR") and category != "cross_section",
        )
        for name, (category, args, returns, text, example) in FUNCTION_TEXT.items()
    ]  # fmt: skip
    fields = [
        FormulaField(
            name=name,
            aliases=sorted(a for a, e in FIELD_NAMES.items() if e.key == FIELD_NAMES[name].key and a != name),
            form=FIELD_NAMES[name].form,
            unit=unit,
            category=category,
            description=text,
        )
        for name, (unit, category, text) in FIELD_TEXT.items()
    ]
    refused = [
        RefusedName(name=n, code="future_function", reason="未来函数：会用到之后的数据") for n in sorted(FUTURE_NAMES)
    ]
    refused += [
        RefusedName(name=n, code="formula_no_data", reason=f"要用{MISSING[w]}，数据里没有") for n, w in NO_DATA.items()
    ]
    refused += [RefusedName(name=n, code="formula_unsupported", reason="暂不支持") for n in UNSUPPORTED]
    refused += [RefusedName(name=n, code="formula_unsupported", reason="画线函数，选股用不上") for n in DRAWING]
    examples = [FormulaExample(**dict(zip(FormulaExample.model_fields, e))) for e in EXAMPLES]
    return Vocab(functions=functions, fields=fields, unsupported=refused, examples=examples)
