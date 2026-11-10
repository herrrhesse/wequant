"""expr check and expr eval: signal expressions in canonical JSON form, checked or evaluated on a panel."""

from __future__ import annotations

import json
from typing import Literal

import numpy as np

from wequant.expr import Expr, ExprError, evaluate, from_form
from wequant.panel import Panel
from wequant.service.base import Context, ErrorDetail, Model, PanelRequest, not_found, open_panel
from wequant.service.details import expr_detail, usage
from wequant.service.rows import Row, rows

Kind = Literal["num", "bool"]


class ExprRequest(Model):
    """A signal expression in canonical form."""

    form: str
    """Canonical form as JSON text, e.g. '["MA","adj_close",20]' or '"close"'."""


class ExprCheck(Model):
    """A valid expression's canonical key and kind."""

    key: str
    """Canonical compact JSON: what results are cached and archived by."""
    kind: Kind
    """num for a number, bool for a condition (1.0/0.0)."""


class EvalRequest(PanelRequest, ExprRequest):
    """A form evaluated for a few codes over a date range."""


class EvalResult(Model):
    """The form's value in each cell, as values.value."""

    data_version: int
    key: str
    kind: Kind
    rows: list[Row]


def expr_check(request: ExprRequest, context: Context = Context()) -> ExprCheck:
    """Build and check the form without data; a refused form is a usage error."""
    expr = parse(request.form)
    return ExprCheck(key=expr.key, kind=expr.kind)


def expr_eval(request: EvalRequest, context: Context = Context()) -> EvalResult:
    """The form's value in each cell of [start, end], computed from the first trading day so windows see all history."""
    expr = parse(request.form)
    panel = open_panel(request, context, history=True)
    first = _first_row(panel, request)
    result = {"data_version": panel.version, "key": expr.key, "kind": expr.kind}
    return EvalResult.model_validate({**result, "rows": rows(panel, {"value": evaluate(expr, panel)}, first)})


def parse(text: str) -> Expr:
    """The checked expression of a form in JSON text; USAGE if the text is not JSON or not a valid form."""
    try:
        form = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise usage([ErrorDetail(code="not_json", message=f"the form is not JSON: {error}")]) from None
    try:
        return from_form(form)
    except ExprError as error:
        raise usage([expr_detail(error)]) from None


def _first_row(panel: Panel, request: EvalRequest) -> int:
    """The row of the first trading day on or after start; NOT_FOUND if there is none up to end."""
    first = 0 if request.start is None else int(np.searchsorted(panel.dates, np.datetime64(request.start, "D")))
    if first == len(panel.dates):
        raise not_found("not_a_trading_day", f"no trading day in [{request.start}, {request.end}]", ["start"])
    return first
