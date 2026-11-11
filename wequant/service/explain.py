"""explain: one stock on one day, from the same cross-section as screen and, with a run, the archived decisions."""

from __future__ import annotations

import datetime as dt
from typing import Literal

import numpy as np
import polars as pl
from pydantic import model_validator
from pydantic_core import PydanticCustomError

from wequant.backtest import Backtest, screen_warnings
from wequant.backtest.anomalies import screen_uses
from wequant.panel import Panel
from wequant.runs.run import Run
from wequant.service.base import Code, Context, ErrorDetail, Model, named, not_found
from wequant.service.details import to_data, usage
from wequant.service.outputs import (
    Action,
    AnomalyUse,
    Op,
    OrderRow,
    Role,
    SellReason,
    Warning,
    anomaly_use,
    order_row,
    warnings_out,
)
from wequant.service.runs import load_run
from wequant.service.screen import (
    FactorCell,
    MemberResult,
    factor_cells,
    finite,
    on_panel,
    strategy_panel,
    target_weights,
    verdict,
)
from wequant.service.specs import DataSpec, IsoDate, Name, StrategyRef, StrategySpec
from wequant.service.strategies import Hash, resolve
from wequant.strategy import Screen, Section, Step, Strategy, comparison, percentiles, pool_items, weight_shares
from wequant.strategy import screen as core_screen
from wequant.strategy.library import strategy_hash


class ExplainRequest(Model):
    """A stock and a day, for a strategy (inline or saved) or an archived run: exactly one of the three."""

    strategy: StrategySpec | None = None
    saved: StrategyRef | None = None
    run: Name | None = None
    code: Code
    date: IsoDate
    """The decision day; with a run also a fill day."""
    data: DataSpec | None = None
    """null: flag without a run, the run's options with one (then it must be null)."""
    data_version: int | None = None
    """null: the current version, or the run's (then it must be null)."""

    @model_validator(mode="after")
    def _exactly_one(self) -> ExplainRequest:
        if sum(x is not None for x in (self.strategy, self.saved, self.run)) != 1:
            raise PydanticCustomError("requires", "give exactly one of strategy, saved and run")
        return self


class PoolCheck(Model):
    """One pool step: bar, universe, then the set pool items."""

    name: str
    passed: bool | None
    """null: no value, which fails too."""
    value: float | bool | str | None
    """bar: t's close; index: membership; boards: the stock's board; exclude_st: its ST flag; min_list_days: list days."""
    note: str | None
    """e.g. listing days counted from the data's first day before S2.3."""


class StepCheck(Model):
    """One filter step, in funnel order."""

    name: str
    kind: Literal["condition", "group"]
    chain: bool
    passed: bool | None
    value: float | None
    op: Op | None
    threshold: float | None
    threshold_hi: float | None
    text: str
    """The expression's canonical form as written."""
    mode: Literal["all", "any"] | None
    members: list[MemberResult] | None


class FactorScore(FactorCell):
    """A factor with its definition."""

    prefer: Literal["high", "low"]
    weight: float


class ScoreCheck(Model):
    """The stock's score among the scored stocks."""

    scored_count: int
    score: float
    rank: int
    factors: list[FactorScore]


class Explain(Model):
    """Why a stock was or was not chosen on a day, and with a run what happened to it."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    date: dt.date
    next_day: dt.date | None
    code: str
    strategy_hash: Hash
    run_id: str | None
    pool: list[PoolCheck]
    steps: list[StepCheck]
    score: ScoreCheck | None
    """null: not scored."""
    missing: str | None
    """Not scored for want of this factor (or formula); null otherwise."""
    role: Role
    weight: float | None
    raw_weight: float | None
    held: bool | None
    """null without a run."""
    action: Action | None
    sell_reason: SellReason | None
    execution: list[OrderRow] | None
    """t+1's orders of this stock, forced exits included; null without a run."""
    anomalies: list[AnomalyUse]
    warnings: list[Warning]


LIST_DAYS_NOTE = "counted from securities.first_date, the first day of the source's data, not the listing date"


def explain(request: ExplainRequest, context: Context = Context()) -> Explain:
    """Why a stock was or was not chosen on a day, read from the screen's own cross-section; with a run, also what the
    run decided and filled."""
    if request.run is not None:
        return _with_run(request, context)
    where = ("strategy",) if request.strategy is not None else ("saved",)
    strategy, warnings = resolve(request, context.library())
    data = to_data(request.data or DataSpec())
    panel = strategy_panel(strategy, request.data_version, data, context, where)
    col = _column(panel, request.code)
    with on_panel(where):
        shot = core_screen(strategy, panel, request.date, context.expr_cache, context.mark)
    row = int(shot.section.rows[0])
    uses = screen_uses(strategy, panel, shot.section, shot.choice)
    weight, raw = target_weights(shot.choice, 0).get(col, (None, None))
    backup = bool(shot.choice.backup[0, col])
    return Explain(
        **named(panel.manifest),
        date=panel.dates[row].item(),
        next_day=panel.dates[row + 1].item() if row + 1 < len(panel.dates) else None,
        code=request.code,
        strategy_hash=strategy_hash(strategy),
        run_id=None,
        **_checks(strategy, panel, shot, col),
        role="target" if weight is not None else "backup" if backup else "none",
        weight=weight,
        raw_weight=raw,
        held=None,
        action=None,
        sell_reason=None,
        execution=None,
        anomalies=[anomaly_use(use) for use in uses.filter(pl.col("code") == request.code).iter_rows(named=True)],
        warnings=warnings + warnings_out(screen_warnings(strategy, panel, shot.section, shot.choice, uses)),
    )


def _with_run(request: ExplainRequest, context: Context) -> Explain:
    """explain on a run: the cross-section recomputed on its store version, anchor and data options; the role, holding,
    action and next day's orders from its archive."""
    for name in ("data", "data_version"):
        if getattr(request, name) is not None:
            detail = ErrorDetail(code="requires", message=f"{name} comes from the run; leave it out", loc=[name])
            raise usage([detail])
    _, run = load_run(context, request.run, ["run"])
    if run.store_version not in context.store().versions():
        raise not_found(
            "version_not_kept", f"run {request.run} read store version {run.store_version}, no longer kept", ["run"]
        )
    spec = Backtest.from_form(run.description)
    day, fill = _decision_day(run, request.date)
    panel = strategy_panel(spec.strategy, run.store_version, spec.data, context, ("run",))
    col = _column(panel, request.code)
    with on_panel(("run",)):
        shot = core_screen(spec.strategy, panel, day, context.expr_cache, context.mark)
    screened = screen_uses(spec.strategy, panel, shot.section, shot.choice)
    code = pl.col("code") == request.code
    pick = run.picks.filter((pl.col("date") == day) & code).rows(named=True)
    decided = run.detail.filter((pl.col("date") == day) & code).rows(named=True)
    pick, decided = (pick or [{}])[0], (decided or [{}])[0]
    action = decided.get("action", "none")
    reason = action == "sell" and SellReason(
        **{key: decided[f"sell_{key}"] for key in ("kind", "step", "rank", "limit")}
    )
    orders = run.ledger.orders.filter((pl.col("date") == fill) & code) if fill else run.ledger.orders.clear()
    uses = run.anomalies.filter(code & pl.col("date").is_in([day, *([fill] if fill else [])]))
    return Explain(
        **named(panel.manifest),
        date=day,
        next_day=fill,
        code=request.code,
        strategy_hash=strategy_hash(spec.strategy),
        run_id=request.run,
        **_checks(spec.strategy, panel, shot, col),
        role=pick.get("role") or "none",
        weight=pick.get("weight"),
        raw_weight=pick.get("raw_weight"),
        held=bool(decided.get("held", False)),
        action=action,
        sell_reason=reason or None,
        execution=[order_row(order) for order in orders.iter_rows(named=True)],
        anomalies=[anomaly_use(use) for use in uses.iter_rows(named=True)],
        warnings=warnings_out(screen_warnings(spec.strategy, panel, shot.section, shot.choice, screened)),
    )


def _checks(strategy: Strategy, panel: Panel, shot: Screen, col: int) -> dict:
    """The pool checks, filter steps, score and missing factor of column col on the screen's day."""
    section, row = shot.section, int(shot.section.rows[0])
    scored = bool(section.scored[0, col])
    missing = shot.rejects.filter((pl.col("code") == panel.codes[col]) & (pl.col("step") == "scored"))["missing"]
    return {
        "pool": _pool(strategy, panel, section, row, col),
        "steps": [_step(step, section, col) for step in strategy.steps()],
        "score": _score(strategy, section, col) if scored else None,
        "missing": missing.item() if missing.len() else None,
    }


def _pool(strategy: Strategy, panel: Panel, section: Section, row: int, col: int) -> list[PoolCheck]:
    """bar, universe, then each set pool item: its verdict and the value it read."""
    values = {
        "index": None,
        "boards": str(panel.boards[col]),
        "exclude_st": bool(panel.st[row, col]) if panel.st_known[row, col] else None,
        "min_list_days": int(panel.list_days[row, col]),
    }
    checks = [
        PoolCheck(
            name="bar", passed=bool(section.traded[0, col]), value=finite(panel.raw("close")[row, col]), note=None
        ),
        PoolCheck(name="universe", passed=verdict(section.steps["universe"][0, col]), value=None, note=None),
    ]
    for name in pool_items(strategy):
        passed = verdict(section.steps[name][0, col])
        value = passed if name == "index" else values[name]
        checks.append(
            PoolCheck(name=name, passed=passed, value=value, note=LIST_DAYS_NOTE if name == "min_list_days" else None)
        )
    return checks


def _step(step: Step, section: Section, col: int) -> StepCheck:
    """One filter step: its verdict, a simple comparison's value and constants, or a group's member verdicts."""
    group = step.expr is None
    compared = None if group else comparison(step.expr)
    members = [MemberResult(name=m, passed=verdict(section.steps[m][0, col])) for m in step.members] if group else None
    return StepCheck(
        name=step.name,
        kind="group" if group else "condition",
        chain=step.chain,
        passed=verdict(section.steps[step.name][0, col]),
        value=finite(section.values[step.name][0, col]) if step.name in section.values else None,
        op=compared and compared.op,
        threshold=compared and compared.threshold,
        threshold_hi=compared and compared.threshold_hi,
        text=f"{step.mode} of {', '.join(step.members)}" if group else step.expr.key,
        mode=step.mode if group else None,
        members=members,
    )


def _score(strategy: Strategy, section: Section, col: int) -> ScoreCheck:
    """A scored stock's score, rank and factor cells with their definitions."""
    defined = {f.name: f for f in strategy.factors}
    cells = factor_cells(weight_shares(strategy), percentiles(strategy, section), section, 0, col)
    factors = [
        FactorScore(**c.model_dump(), prefer=defined[c.name].prefer, weight=defined[c.name].weight) for c in cells
    ]
    return ScoreCheck(
        scored_count=int(section.scored[0].sum()),
        score=section.score[0, col],
        rank=section.rank[0, col],
        factors=factors,
    )


def _column(panel: Panel, code: str) -> int:
    """The panel column of code; NOT_FOUND unknown_code at ["code"] when the store has no such code."""
    found = np.flatnonzero(panel.codes == code)
    if not found.size:
        raise not_found("unknown_code", f"store version {panel.version} has no code {code}", ["code"])
    return int(found[0])


def _decision_day(run: Run, day: dt.date) -> tuple[dt.date, dt.date | None]:
    """The run's decision day for day (day itself, or the decision day day fills) and its fill day; USAGE
    not_a_decision_day otherwise."""
    days = run.ledger.daily["date"].to_list()
    decided = set(run.funnel["date"].to_list())
    position = days.index(day) if day in days else -1
    if day in decided:
        decision = day
    elif position > 0 and days[position - 1] in decided:
        decision = days[position - 1]
    else:
        message = f"{day} is neither a decision day nor a fill day of run {run.id}"
        raise usage([ErrorDetail(code="not_a_decision_day", message=message, loc=["date"])])
    following = days.index(decision) + 1
    return decision, days[following] if following < len(days) else None
