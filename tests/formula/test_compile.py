"""S4.1: formula text compiles to the tree the expression builders make, with TDX's names, units and habits."""

import pytest

from wequant.expr import (
    ABS,
    AVEDEV,
    BARSCOUNT,
    BARSLAST,
    BETWEEN,
    CORR,
    COUNT,
    CROSS,
    CS_RANK,
    EMA,
    EQ,
    EVERY,
    EXIST,
    FUTURE,
    HHV,
    IF,
    LLV,
    LN,
    MA,
    MAX,
    MIN,
    NOT,
    REF,
    SLOPE,
    SMA,
    STD,
    STDP,
    SUM,
    TS_RANK,
    VALUEWHEN,
    WEEK,
    WMA,
    Codes,
    Field,
)
from wequant.formula import FormulaError, compile_formula

C, OP, H, L = (Field(f"adj_{name}") for name in ("close", "open", "high", "low"))
VOL, AMOUNT = Field("volume") / 100, Field("amount")  # TDX's VOL counts lots of 100 shares
RSV = (C - LLV(L, 9)) / (HHV(H, 9) - LLV(L, 9)) * 100
K = SMA(RSV, 3, 1)
D = SMA(K, 3, 1)
J = 3 * K - 2 * D
USER = """N:=9;M1:=3;M2:=3;
RSV:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;
K:=SMA(RSV,M1,1);
D:=SMA(K,M2,1);
J:=3*K-2*D;
{本周 J>K>D}
A:= J>K AND K>D;
{上周（前1根周K线）}
J1:=REF(J,1);
K1:=REF(K,1);
D1:=REF(D,1);
{两两相对差值≤10%，分母>0防除零}
COND_JK:= ABS(J1-K1)/MAX(J1,K1)<=0.10 AND MAX(J1,K1)>0;
COND_KD:= ABS(K1-D1)/MAX(K1,D1)<=0.10 AND MAX(K1,D1)>0;
COND_JD:= ABS(J1-D1)/MAX(J1,D1)<=0.10 AND MAX(J1,D1)>0;
B:= COND_JK AND COND_KD AND COND_JD;
{上上周（前2根周K线）J>K>D}
C:= REF(J,2)>REF(K,2) AND REF(K,2)>REF(D,2);
XG: A AND B AND C;
"""


def near(x, y):
    return (ABS(x - y) / MAX(x, y) <= 0.10) & (MAX(x, y) > 0)


J1, K1, D1 = REF(J, 1), REF(K, 1), REF(D, 1)
USER_TREE = WEEK(
    ((J > K) & (K > D))
    & (near(J1, K1) & near(K1, D1) & near(J1, D1))
    & ((REF(J, 2) > REF(K, 2)) & (REF(K, 2) > REF(D, 2)))
)
SAMPLES = [  # (text, period, the tree the builders make)
    ("C>5", "day", C > 5),
    ("CLOSE>MA(CLOSE,20)", "day", C > MA(C, 20)),
    ("C/REF(C,20)-1", "day", C / REF(C, 20) - 1),
    ("MA(AMOUNT,20)", "day", MA(AMOUNT, 20)),
    ("NOT EXCHANGE('BJ')", "day", NOT(Field("exchange@BJ"))),
    ("RAW_CLOSE>5", "day", Field("close") > 5),
    ("VOL>MA(VOL,5)*2", "day", VOL > MA(VOL, 5) * 2),
    ("CROSS(MA(C,5),MA(C,20))", "day", CROSS(MA(C, 5), MA(C, 20))),
    ("EVERY(C>O,3)", "day", EVERY(C > OP, 3)),
    ("EXIST(C>REF(C,1)*1.09,10)", "day", EXIST(C > REF(C, 1) * 1.09, 10)),
    ("COUNT(C>O,5)>=3", "day", COUNT(C > OP, 5) >= 3),
    ("HHV(H,20)", "day", HHV(H, 20)),
    ("LLV(L,20)", "day", LLV(L, 20)),
    ("(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100", "day", RSV),
    ("SMA(C,3,1)", "day", SMA(C, 3, 1)),
    ("EMA(C,12)-EMA(C,26)", "day", EMA(C, 12) - EMA(C, 26)),
    ("EXPMA(C,12)", "day", EMA(C, 12)),
    ("WMA(C,10)", "day", WMA(C, 10)),
    ("STD(C,20)/MA(C,20)", "day", STD(C, 20) / MA(C, 20)),
    ("STDP(C,20)", "day", STDP(C, 20)),
    ("AVEDEV(C,14)", "day", AVEDEV(C, 14)),
    ("SLOPE(C,20)", "day", SLOPE(C, 20)),
    ("SUM(VOL,5)", "day", SUM(VOL, 5)),
    ("SUM(VOL,0)", "day", SUM(VOL, 0)),
    ("BARSLAST(CROSS(C,MA(C,20)))", "day", BARSLAST(CROSS(C, MA(C, 20)))),
    ("BARSCOUNT(C)", "day", BARSCOUNT(C)),
    ("VALUEWHEN(CROSS(C,MA(C,20)),C)", "day", VALUEWHEN(CROSS(C, MA(C, 20)), C)),
    ("IF(C>O,1,-1)", "day", IF(C > OP, 1.0, -1.0)),
    ("IFF(C>O,C,O)", "day", IF(C > OP, C, OP)),
    ("BETWEEN(C/MA(C,20),0.95,1.05)", "day", BETWEEN(C / MA(C, 20), 0.95, 1.05)),
    ("MAX(O,C)-MIN(O,C)", "day", MAX(OP, C) - MIN(OP, C)),
    ("ABS(C-O)/O", "day", ABS(C - OP) / OP),
    ("LN(C)-LN(REF(C,1))", "day", LN(C) - LN(REF(C, 1))),
    ("C>O AND C>REF(C,1)", "day", (C > OP) & (C > REF(C, 1))),
    ("C>O OR C<REF(C,1)", "day", (C > OP) | (C < REF(C, 1))),
    ("C>O && V>0", "day", (C > OP) & (VOL > 0)),
    ("NOT(C>O)", "day", NOT(C > OP)),
    ("C<>O", "day", NOT(EQ(C, OP))),
    ("C=O", "day", EQ(C, OP)),
    ("-C", "day", 0 - C),
    ("C*-1", "day", C * -1),
    ("1+2*C", "day", 1 + 2 * C),
    ("C-O-H", "day", C - OP - H),
    ("N:=5; MA(C,N)", "day", MA(C, 5)),
    ("N:=5; M:=N*2; MA(C,M)", "day", MA(C, 10)),
    ("A:=C>O; B:=V>MA(V,5); XG: A AND B;", "day", (C > OP) & (VOL > MA(VOL, 5))),
    ("COUNT(C,5)", "day", COUNT(NOT(EQ(C, 0)), 5)),  # a number where a condition belongs: not 0
    ("NOT ST AND LIST_DAYS>60", "day", NOT(Field("st")) & (Field("list_days") > 60)),
    ("MEMBER('000300.SH') AND BOARD('main')", "day", Field("member@000300.SH") & Field("board@main")),
    ("CODES('600000.SH','000001.SZ')", "day", Codes(["600000.SH", "000001.SZ"])),
    ("WEEK(C>MA(C,10)) AND C>MA(C,20)", "day", WEEK(C > MA(C, 10)) & (C > MA(C, 20))),
    ("CS_RANK(C/REF(C,20))", "day", CS_RANK(C / REF(C, 20))),
    ("TS_RANK(VOL,20)+CORR(C,VOL,20)", "day", TS_RANK(VOL, 20) + CORR(C, VOL, 20)),
    ("{comment} C > 1 {another}", "day", C > 1),
    ("c>ma(c,5)", "day", C > MA(C, 5)),
    ("C＞MA（C，5）", "day", C > MA(C, 5)),
    ("XG: C>MA(C,5), COLORRED, NODRAW;", "day", C > MA(C, 5)),
    ("A:C>O; B:C<O;", "day", C < OP),
    ("PRECLOSE+RAW_VOLUME", "day", Field("adj_preclose") + Field("volume")),
    ("C>MA(C,10)", "week", WEEK(C > MA(C, 10))),
    (USER, "week", USER_TREE),
]


@pytest.mark.parametrize(("text", "period", "tree"), SAMPLES, ids=[f"{i}" for i in range(len(SAMPLES))])
def test_a_formula_compiles_to_the_builders_tree(text, period, tree):
    assert compile_formula(text, period).expr.key == tree.key


def test_the_user_formula_names_its_output_and_lines():
    compiled = compile_formula(USER, "week", "condition")
    assert compiled.output == "XG" and [v.name for v in compiled.variables][:5] == ["N", "M1", "M2", "RSV", "K"]
    assert [(v.kind, v.value) for v in compiled.variables[:3]] == [("const", 9.0), ("const", 3.0), ("const", 3.0)]


def test_a_line_named_like_a_built_in_name_means_that_line_from_there_on():
    """The user's formula names a line C: C is the close before it and that line after it, with a warning."""
    compiled = compile_formula("X:=C>O; C:=C<O; XG: C;")
    assert compiled.expr.key == (C < OP).key and [w.code for w in compiled.warnings] == ["formula_shadows"]


def test_a_condition_role_refuses_a_number_as_output():
    with pytest.raises(FormulaError) as refused:
        compile_formula("XG: MA(C,5);", role="condition")
    assert refused.value.code == "formula_needs_bool"
    assert compile_formula("XG: MA(C,5);", role="factor").expr.key == MA(C, 5).key


def test_a_back_adjusted_price_compared_with_a_constant_is_warned():
    """C is back-adjusted, so C > 5 compares a level that is not the traded price; RAW_CLOSE > 5 does not warn."""
    assert [w.code for w in compile_formula("MA(C,5)>5").warnings] == ["adjusted_level"]
    assert compile_formula("RAW_CLOSE>5").warnings == () and compile_formula("C/REF(C,1)>1.05").warnings == ()


@pytest.mark.parametrize("name", sorted(FUTURE))
def test_a_future_function_is_refused(name):
    with pytest.raises(FormulaError) as refused:
        compile_formula(f"XG: {name}(C,5)>0;")
    assert refused.value.code == "future_function"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("XG: FINANCE(1)>0;", "formula_no_data"),
        ("XG: SQRT(C)>1;", "formula_unsupported"),
        ("STICKLINE(C>O,O,C,1,0);", "formula_unsupported"),
        ("XG: MACD>0;", "formula_unknown_name"),
        ("XG: MA>0;", "formula_unknown_name"),
        ("A:=1; B:=2;", "formula_no_output"),
        ("A:=C; A:=O; A;", "formula_redefined"),
        ("XG: MA(C,V)>1;", "formula_not_constant"),
        ("XG: C>MA(C,5);", None),
    ],
)
def test_a_name_the_formula_cannot_use_says_why(text, code):
    if code is None:
        assert compile_formula(text).expr.kind == "bool"
        return
    with pytest.raises(FormulaError) as refused:
        compile_formula(text)
    assert refused.value.code == code


@pytest.mark.parametrize(
    ("text", "period", "code"),
    [
        ("XG: ST;", "week", "period_field"),
        ("XG: MEMBER('000300.SH');", "week", "period_field"),
        ("XG: CS_RANK(C)>0.5;", "week", "period_cross_section"),
        ("XG: WEEK(C>O);", "month", "period_nested"),
    ],
)
def test_a_weekly_formula_reads_only_what_bars_have(text, period, code):
    with pytest.raises(FormulaError) as refused:
        compile_formula(text, period)
    assert refused.value.code == code
