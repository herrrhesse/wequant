"""The names a formula can use: TDX's price and volume aliases, wequant's other fields, the functions mapped onto
wequant.expr's operators, and the TDX names it refuses with the reason (S4.1)."""

from __future__ import annotations

from types import MappingProxyType

from wequant.expr import DESCRIPTIVE, FIELDS, FUTURE, OPERATORS, PLANNED, Expr, Field

PRICES = {"OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close", "PRECLOSE": "preclose"}
SHORT = {"O": "OPEN", "H": "HIGH", "L": "LOW", "C": "CLOSE", "V": "VOL", "VOLUME": "VOL", "AMO": "AMOUNT"}
LOT = 100.0  # shares in a lot: TDX's VOL counts lots


def _fields() -> dict[str, Expr]:
    """Text name -> the tree it reads: C/O/H/L/PRECLOSE back-adjusted, RAW_ the unadjusted, VOL in lots, AMOUNT in
    yuan, RAW_VOLUME in shares, every other field by its own name."""
    found: dict[str, Expr] = {name: Field(f"adj_{field}") for name, field in PRICES.items()}
    found |= {f"RAW_{name}": Field(field) for name, field in PRICES.items()}
    found |= {"VOL": Field("volume") / LOT, "RAW_VOLUME": Field("volume"), "AMOUNT": Field("amount")}
    found |= {name.upper(): Field(name) for name in FIELDS if name.upper() not in found and name not in RAW}
    found |= {short: found[long] for short, long in SHORT.items()}
    return found


RAW = ("open", "high", "low", "close", "preclose", "volume", "amount")  # read through the aliases above
FIELD_NAMES: MappingProxyType = MappingProxyType(_fields())
REFUSED_FIELDS = {name.upper(): name for name in (*DESCRIPTIVE, *PLANNED)}  # Field() itself says why
INTERNAL = ("ADD", "SUB", "MUL", "DIV", "GT", "GE", "LT", "LE", "EQ", "AND", "OR")  # written as operators in text
FUNCTIONS = {name: name for name in OPERATORS if name not in INTERNAL} | {"EXPMA": "EMA", "IFF": "IF"}
FAMILY_FUNCTIONS = {"MEMBER": "member", "BOARD": "board", "EXCHANGE": "exchange"}
PERIOD_FUNCTIONS = {"WEEK": "week", "MONTH": "month"}
NO_DATA = {  # TDX functions whose data the store does not have -> what is missing (labels' hints say it in Chinese)
    **dict.fromkeys(("FINANCE", "FINVALUE", "FINONE", "GPJYVALUE", "GPJYONE"), "financial statements"),
    **dict.fromkeys(("DYNAINFO",), "real-time quotes"),
    **dict.fromkeys(("CAPITAL", "TOTALCAPITAL"), "share capital"),
    **dict.fromkeys(
        ("INDEXC", "INDEXO", "INDEXH", "INDEXL", "INDEXV", "INDEXA", "INDEXADV", "INDEXDEC"), "market index bars"
    ),
    **dict.fromkeys(("ADVANCE", "DECLINE"), "advancing and declining counts"),
    **dict.fromkeys(("BUYVOL", "SELLVOL", "ISBUYORDER"), "tick trades"),
    **dict.fromkeys(("WINNER", "LWINNER", "PWINNER", "COST", "COSTEX", "PPART"), "chip distribution"),
    **dict.fromkeys(("HYZSCODE", "HYBLOCK", "GNBLOCK", "INBLOCK", "BLOCKSETNUM"), "industries and sectors"),
    **dict.fromkeys(("DATE", "TIME", "YEAR", "MONTH", "DAY", "WEEKDAY", "HOUR", "MINUTE", "PERIOD"), "dates and times"),
    **dict.fromkeys(("CODE", "SETCODE", "STKNAME", "STKINDI"), "codes and names"),
}
UNSUPPORTED = (  # TDX functions wequant has no operator for yet
    "SQRT POW EXP LOG MOD INTPART ROUND FLOOR CEILING SIN COS TAN ASIN ACOS ATAN SIGN REVERSE MEMA DMA TMA AMA TRMA "
    "FORCAST DEVSQ VAR VARP COVAR RELATE BETA RANGE LONGCROSS UPNDAY DOWNNDAY NDAY LAST TFILTER FILTER SAR SARTURN "
    "HOD LOD SUMBARS BARSSINCE BARSSINCEN ISUP ISDOWN ISEQUAL CONST2 ZTPRICE DTPRICE"
).split()
DRAWING = (  # drawing functions: nothing to select with
    "DRAWTEXT DRAWTEXT_FIX DRAWICON DRAWKLINE DRAWBAND DRAWNUMBER DRAWNUMBER_FIX DRAWSL DRAWGBK STICKLINE "
    "POLYLINE PLOYLINE PARTLINE FILLRGN BUY SELL BUYSHORT SELLSHORT TIPS DRAWNULL"
).split()
INDICATORS = "MACD KDJ KD RSI BOLL WR CCI DMI OBV ASI BIAS ROC MTM PSY VR ARBR CR TRIX DMA EMV".split()
EXTRA_FUTURE = ("DHIGH", "DOPEN", "DLOW", "DCLOSE", "DVOL", "ZIGA")  # also 未来函数 in TDX's function list
FUTURE_NAMES = frozenset(FUTURE) | frozenset(EXTRA_FUTURE)


def known() -> list[str]:
    """Every name a formula can use, for suggestions."""
    return sorted({*FIELD_NAMES, *FUNCTIONS, *FAMILY_FUNCTIONS, *PERIOD_FUNCTIONS, "CODES", "NOT", "AND", "OR"})
