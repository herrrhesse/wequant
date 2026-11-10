"""One builder per operator, named, called and documented like its wequant.ops function, returning an Op."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from wequant.expr.tree import ExprError, Op
from wequant.expr.vocab import OPERATORS


def _builder(name: str) -> Callable[..., Op]:
    """A function with the signature and docstring of the ops function name that builds Op(name, ...)."""
    function = OPERATORS[name].function
    signature = inspect.signature(function)

    def build(*args, **kwargs) -> Op:
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError as error:
            raise ExprError("bad_arity", f"{name}: {error}") from None
        bound.apply_defaults()
        return Op(name, *bound.args)

    build.__name__, build.__qualname__, build.__doc__ = function.__name__, function.__qualname__, function.__doc__
    build.__signature__ = signature
    return build


REF = _builder("REF")
MA = _builder("MA")
EMA = _builder("EMA")
SMA = _builder("SMA")
WMA = _builder("WMA")
SUM = _builder("SUM")
HHV = _builder("HHV")
LLV = _builder("LLV")
HHVBARS = _builder("HHVBARS")
LLVBARS = _builder("LLVBARS")
STD = _builder("STD")
STDP = _builder("STDP")
AVEDEV = _builder("AVEDEV")
SLOPE = _builder("SLOPE")
COUNT = _builder("COUNT")
EVERY = _builder("EVERY")
EXIST = _builder("EXIST")
TS_RANK = _builder("TS_RANK")
CORR = _builder("CORR")
CROSS = _builder("CROSS")
BARSLAST = _builder("BARSLAST")
BARSCOUNT = _builder("BARSCOUNT")
VALUEWHEN = _builder("VALUEWHEN")
CS_RANK = _builder("CS_RANK")
CS_PCT = _builder("CS_PCT")
CS_ZSCORE = _builder("CS_ZSCORE")
CS_DEMEAN = _builder("CS_DEMEAN")
GT = _builder("GT")
GE = _builder("GE")
LT = _builder("LT")
LE = _builder("LE")
EQ = _builder("EQ")
AND = _builder("AND")
OR = _builder("OR")
NOT = _builder("NOT")
IF = _builder("IF")
BETWEEN = _builder("BETWEEN")
MAX = _builder("MAX")
MIN = _builder("MIN")
ABS = _builder("ABS")
DIV = _builder("DIV")
LN = _builder("LN")
ADD = _builder("ADD")
SUB = _builder("SUB")
MUL = _builder("MUL")
