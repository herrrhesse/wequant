"""formula check, trial and format (S4.1): TDX-style formula text checked as it is typed, its every variable computed
bar by bar for one stock to compare with TDX, and expression trees written back as text."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np
from pydantic import Field, JsonValue

from wequant.expr import Expr, ExprError, Op, evaluate_all, from_form
from wequant.expr.vocab import CROSS_SECTIONAL
from wequant.formula import MAX_TEXT, Compiled, FormulaError, compile_formula, format_formula
from wequant.formula.names import LOT
from wequant.panel import NotInPanel, load_panel
from wequant.service.base import (
    Code,
    Context,
    DataRequest,
    ErrorDetail,
    Model,
    TextPosition,
    manifest,
    named,
    names_of,
    not_found,
    read_version,
)
from wequant.service.details import usage
from wequant.service.hints import hint
from wequant.service.schedule import following
from wequant.service.specs import IsoDate

Period = Literal["day", "week", "month"]
Role = Literal["condition", "factor", "universe", "formula", "sell", "keep"]
Kind = Literal["num", "bool"]


class FormulaRequest(Model):
    """A formula's text, its period and, optionally, what it is for."""

    text: str = Field(max_length=MAX_TEXT)
    period: Period = "day"
    role: Role | None = None
    """condition, universe, sell and keep need a condition as output; factor and formula a number (a condition
    counts 1/0); null: either."""


class FormulaIssue(Model):
    """An error or a warning about a formula, at its place in the text (lines and columns from 1)."""

    code: str
    message: str
    """English."""
    hint: str | None
    """The rule in one Chinese sentence, naming the words at fault."""
    line: int | None
    col: int | None
    end_line: int | None
    end_col: int | None
    """Just after the last character at fault."""
    token: str | None


class FormulaVariable(Model):
    """A line of the formula: a named one, or an unnamed output (NONAME0, NONAME1, ... as TDX names them)."""

    name: str
    line: int
    kind: Literal["num", "bool", "const"]
    value: float | None
    """A constant's value; null otherwise."""
    output: bool
    """An output line (NAME: or unnamed); the last is the formula's output."""
    used: bool
    """Read by a later line, or the formula's output."""


class FormulaCheck(Model):
    """A checked formula: its tree when it compiles, its lines, and what is wrong or doubtful."""

    ok: bool
    tree: JsonValue
    """The compiled tree (under WEEK or MONTH for those periods); null when it does not compile."""
    key: str | None
    """The tree's canonical JSON: what a strategy's hash is made of."""
    kind: Kind | None
    output: str | None
    """The output line's name; null for an unnamed one or when it does not compile."""
    variables: list[FormulaVariable]
    errors: list[FormulaIssue]
    """At most one: the first place the text breaks a rule."""
    warnings: list[FormulaIssue]
    """adjusted_level (a back-adjusted price compared with a constant), formula_shadows (a line named like a built-in
    name)."""


def formula_check(request: FormulaRequest, context: Context = Context()) -> FormulaCheck:
    """Compile the formula without data; what is wrong comes back in errors, not as a failure."""
    try:
        compiled = compile_formula(request.text, request.period, request.role)
    except (FormulaError, ExprError) as error:
        return FormulaCheck(
            ok=False, tree=None, key=None, kind=None, output=None, variables=[], errors=[_issue(error)], warnings=[]
        )
    return FormulaCheck(
        ok=True,
        tree=compiled.expr.form,
        key=compiled.expr.key,
        kind=compiled.expr.kind,
        output=compiled.output,
        variables=[
            FormulaVariable(**{k: getattr(v, k) for k in FormulaVariable.model_fields}) for v in compiled.variables
        ],
        errors=[],
        warnings=[_issue(w) for w in compiled.warnings],
    )


class TrialRequest(DataRequest):
    """A formula computed for one stock, to compare with TDX bar by bar."""

    text: str = Field(max_length=MAX_TEXT)
    period: Period = "day"
    code: Code
    start: IsoDate | None = None
    """First bar shown (default: as many as max_bars allows)."""
    end: IsoDate | None = None
    """Last bar shown (default: the store's last)."""
    anchor: IsoDate | None = None
    """The trading day the formula counts from, as a strategy's anchor; null: the store's first day."""
    max_bars: int = Field(120, ge=1, le=1000)
    """At most this many bars, the latest ones."""


class TdxPrices(Model):
    """Forward-adjusted prices to the store's last day (raw × factor ÷ that day's factor): only for comparing price
    levels with TDX's 前复权 charts; the formula's C is back-adjusted, and ratios such as K, D, J are the same in both."""

    open: float | None
    high: float | None
    low: float | None
    close: float | None


class TrialColumn(Model):
    """A variable or output shown, in the formula's order; constants are not."""

    name: str
    kind: Kind
    output: bool


class TrialRow(Model):
    """One bar: a day, or a week or month ending on date."""

    date: dt.date
    """The bar's last trading day."""
    start: dt.date
    """The bar's first trading day (the period's, for a weekly or monthly bar)."""
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    """Back-adjusted, as the formula's O, H, L, C read them."""
    volume: float | None
    """In lots, as the formula's VOL reads it."""
    raw_close: float | None
    """Unadjusted close."""
    tdx: TdxPrices
    values: dict[str, float | None]
    """Each column's value; a condition's is 1 or 0; null: none (too few bars, or a division by 0)."""


class Trial(Model):
    """The formula's lines on one stock's bars."""

    data_version: int
    data_version_name: str
    code: str
    name: str | None
    period: Period
    columns: list[TrialColumn]
    rows: list[TrialRow]
    open_period: dt.date | None
    """The store's last day when its week or month has not ended, so its bar is not shown; null otherwise."""


def formula_trial(request: TrialRequest, context: Context = Context()) -> Trial:
    """The formula's every variable and output on each bar of one stock from start to end, computed from the anchor;
    a formula with a cross-sectional function ranks among every stock."""
    compiled = _compiled(request.text, request.period)
    store = context.store()
    found = manifest(store, read_version(context, request.data_version))
    after = following(store, found)[0] if request.period != "day" else None
    ranked = any(_ranks(v.expr) for v in compiled.variables if v.expr is not None)
    try:
        panel = load_panel(
            request.anchor,
            None,
            None if ranked else [request.code],
            version=found["version"],
            store=store,
            cache_dir=context.cache,
            after=after,
        )
    except NotInPanel as error:
        raise not_found(error.code, error.args[0], ["code" if error.code == "unknown_code" else "anchor"]) from None
    col = 0 if not ranked else int(np.flatnonzero(panel.codes == request.code)[0])
    bars = panel if request.period == "day" else panel.periods(request.period)
    shown = [v for v in compiled.variables if v.expr is not None]
    values = evaluate_all({v.name: v.expr for v in shown}, bars) if shown else {}
    rows = _rows(request, panel, bars, col, values)
    last_open = request.period != "day" and (not len(bars.dates) or bars.dates[-1] != panel.dates[-1])
    return Trial(
        **named(found),
        code=request.code,
        name=names_of(context, [request.code]).get(request.code),
        period=request.period,
        columns=[TrialColumn(name=v.name, kind=v.kind, output=v.output) for v in shown],
        rows=rows,
        open_period=panel.dates[-1].item() if last_open and request.end is None else None,
    )


def _rows(request: TrialRequest, panel, bars, col: int, values: dict) -> list[TrialRow]:
    """The bars of the stock from start to end, the last max_bars of them."""
    dates = bars.dates
    firsts = panel.dates[bars.first_rows] if bars is not panel else dates
    keep = np.asarray(bars.traded[:, col])
    if request.start is not None:
        keep &= dates >= np.datetime64(request.start, "D")
    if request.end is not None:
        keep &= dates <= np.datetime64(request.end, "D")
    picked = np.flatnonzero(keep)[-request.max_bars :]
    prices = {name: bars.adjusted(name)[:, col] for name in ("open", "high", "low", "close")}
    volume, raw_close, base = bars.raw("volume")[:, col] / LOT, bars.raw("close")[:, col], panel.factor[-1, col]
    return [
        TrialRow(
            date=dates[i].item(),
            start=firsts[i].item(),
            **{name: _finite(grid[i]) for name, grid in prices.items()},
            volume=_finite(volume[i]),
            raw_close=_finite(raw_close[i]),
            tdx=TdxPrices(**{name: _finite(grid[i] / base) for name, grid in prices.items()}),
            values={name: _finite(grid[i, col]) for name, grid in values.items()},
        )
        for i in picked.tolist()
    ]


class FormatRequest(Model):
    """Expression trees to write as formula text."""

    forms: list[JsonValue] = Field(min_length=1, max_length=500)
    """Canonical forms, as JSON values (a field name or [operator, arguments...])."""


class FormattedFormula(Model):
    """One tree as text: parsing it with its period gives the same tree back."""

    text: str | None
    period: Period | None
    error: ErrorDetail | None
    """Why the tree has no text: it is not a valid tree, or it holds an empty argument; null otherwise."""


class Formatted(Model):
    formulas: list[FormattedFormula]
    """One per form, in order."""


def formula_format(request: FormatRequest, context: Context = Context()) -> Formatted:
    """Each tree written as TDX-style text, without variables; a WEEK or MONTH at its root becomes the period."""
    return Formatted(formulas=[_formatted(form, i) for i, form in enumerate(request.forms)])


def _formatted(form: object, i: int) -> FormattedFormula:
    try:
        text, period = format_formula(from_form(form))
    except ExprError as error:
        detail = ErrorDetail(code=error.code, message=str(error), loc=["forms", i], path=list(error.path))
        return FormattedFormula(text=None, period=None, error=detail)
    except ValueError as error:
        return FormattedFormula(
            text=None, period=None, error=ErrorDetail(code="not_a_form", message=str(error), loc=["forms", i])
        )
    return FormattedFormula(text=text, period=period, error=None)


def _compiled(text: str, period: str) -> Compiled:
    """The compiled formula; USAGE located at the text when it does not compile."""
    try:
        return compile_formula(text, period)
    except FormulaError as error:
        position = None if error.position is None else TextPosition(**error.position)
        raise usage([ErrorDetail(code=error.code, message=str(error), loc=["text"], position=position)]) from None
    except ExprError as error:
        raise usage([ErrorDetail(code=error.code, message=str(error), loc=["text"])]) from None


def _issue(error: FormulaError | ExprError) -> FormulaIssue:
    """The issue of an error or warning, with its Chinese hint."""
    place = getattr(error, "position", None) or {}
    return FormulaIssue(
        code=error.code,
        message=str(error),
        hint=hint({"code": error.code, "loc": ["text"], "message": str(error)}),
        line=place.get("line"),
        col=place.get("col"),
        end_line=place.get("end_line"),
        end_col=place.get("end_col"),
        token=place.get("token"),
    )


def _ranks(expr: Expr) -> bool:
    """expr holds a cross-sectional operator."""
    return isinstance(expr, Op) and (
        expr.name in CROSS_SECTIONAL or any(isinstance(a, Expr) and _ranks(a) for a in expr.args)
    )


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None
