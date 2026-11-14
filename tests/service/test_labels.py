"""labels: one Chinese label per code the code base defines, and warning templates that fill from each warning's
params (F-19, R-10, R-11)."""

import ast
from pathlib import Path
from typing import get_args

import pytest

import wequant
from wequant import service
from wequant.account.delisting import TREATMENTS
from wequant.account.ledger import ORDER_REASONS
from wequant.analytics.inputs import ORDER_STATUSES
from wequant.analytics.metrics import REASONS, UNITS
from wequant.backtest.messages import TEXTS
from wequant.data.delistings import KINDS
from wequant.data.indexes import INDEXES
from wequant.data.schema import CLASSES, ISSUE_KINDS, LEVELS
from wequant.data.store import REASONS as VERSION_REASONS
from wequant.pools import REASONS as PARSE_REASONS
from wequant.runs.causes import CAUSES, PARAMS
from wequant.service import Context
from wequant.service.build import BuildResult
from wequant.service.details import PYDANTIC
from wequant.service.errors import Exit
from wequant.service.indicators import Category
from wequant.service.jobs import KINDS as JOB_KINDS
from wequant.service.jobs import JobKind, JobState
from wequant.service.labels import fill, job_text, update_params
from wequant.service.own import Rule
from wequant.service.pools import SCOPE_TEXTS
from wequant.service.queue import STAGES as QUEUE_STAGES
from wequant.service.queue import STATES
from wequant.service.running import STAGES as WORKER_STAGES
from wequant.service.schedule import Source
from wequant.service.screen import StepKind
from wequant.service.update import Status as UpdateStatus
from wequant.strategy import SELL_KINDS
from wequant.strategy.library import READ_TEXTS

ERRORS = {"SpecError", "ExprError", "LookAheadError", "NotSaved", "NotInPanel", "not_found", "PydanticCustomError"}
ERRORS |= {"Refused"}  # wequant.storage.relocate: the folder refusals the data root operations report by their code
LABELS = service.labels(service.LabelsRequest())


def error_codes() -> set[str]:
    """Every ErrorDetail code the code base can produce: the first argument of the errors that carry one, ErrorDetail's
    code keyword, and the codes pydantic errors map to."""
    found = set(PYDANTIC.values()) | {"wrong_type"}
    for path in Path(wequant.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            name = getattr(getattr(node, "func", None), "id", getattr(getattr(node, "func", None), "attr", None))
            if name in ERRORS and node.args and isinstance(node.args[0], ast.Constant):
                found.add(node.args[0].value)
            if name == "ErrorDetail":
                found |= {k.value.value for k in node.keywords if k.arg == "code" and isinstance(k.value, ast.Constant)}
    return found


def metric_names(market_home, cache) -> set[str]:
    """The metrics a backtest reports."""
    run = service.backtest(service.BacktestRequest(saved={"id": "m"}, save=False), Context(market_home, cache))
    return set(run.metrics)


FAMILIES = {  # labels field -> the codes the code base defines for it
    "warnings": lambda: set(TEXTS) | set(READ_TEXTS) | set(SCOPE_TEXTS),
    "issue_kinds": lambda: set(ISSUE_KINDS),
    "issue_actions": lambda: {a for kind in ISSUE_KINDS.values() for a in kind.actions},
    "metric_reasons": lambda: set(REASONS),
    "units": lambda: set(UNITS),
    "funnel_kinds": lambda: set(get_args(StepKind)),
    "order_statuses": lambda: set(ORDER_STATUSES),
    "order_reasons": lambda: set(ORDER_REASONS),
    "sell_kinds": lambda: set(SELL_KINDS),
    "delisting_treatments": lambda: set(TREATMENTS),
    "delisting_kinds": lambda: set(KINDS),
    "anomaly_classes": lambda: set(CLASSES),
    "anomaly_levels": lambda: set(LEVELS),
    "error_codes": error_codes,
    "exits": lambda: {e.name for e in Exit},
    "version_reasons": lambda: set(VERSION_REASONS),
    "job_kinds": lambda: set(get_args(JobKind)) | set(JOB_KINDS),
    "job_states": lambda: set(get_args(JobState)) | set(STATES),
    "job_stages": lambda: set(QUEUE_STAGES) | set(WORKER_STAGES),
    "update_statuses": lambda: set(get_args(UpdateStatus)),
    "order_causes": lambda: set(CAUSES),
    "day_sources": lambda: set(get_args(Source)),
    "scopes": lambda: {"all", "stocks", *INDEXES},
    "parse_reasons": lambda: set(PARSE_REASONS),
    "indicator_categories": lambda: set(get_args(Category)),
    "own_rules": lambda: set(get_args(Rule)),
    "build_statuses": lambda: set(get_args(BuildResult.model_fields["status"].annotation)),
}
STAGE_PARAMS = {  # the params each job stage reports: the queue's, the worker's and JobInfo's own
    "queued": ["position"],
    "backtest": ["day", "done", "total", "equity"],
    "failed": ["reason"],
    "crashed": ["exit_code"],
    "update_snapshot": ["day", "done", "total"],
    "update_factors": ["done", "total"],
    "relocate_copy": ["done_mb", "total_mb"],
    "build_stocks": ["code", "done", "total"],
    "build_factors": ["done", "total"],
}


@pytest.mark.parametrize("family", FAMILIES)
def test_each_code_of_the_code_base_has_exactly_one_label(family):
    listed = [label.code for label in getattr(LABELS, family)]
    assert sorted(listed) == sorted(set(listed)) == sorted(FAMILIES[family]()), family


def test_each_metric_a_backtest_reports_has_a_label(market_home, cache):
    assert {label.code for label in LABELS.metrics} == metric_names(market_home, cache)


class Reads(dict):
    """params whose every value is 1, recording which keys a text reads."""

    def __init__(self, keys):
        super().__init__(dict.fromkeys(keys, 1))
        self.read = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)


@pytest.mark.parametrize("label", LABELS.warnings, ids=[w.code for w in LABELS.warnings])
def test_a_warning_template_uses_exactly_the_params_its_english_text_reads(label):
    """The English text names every number and date of a warning: its params; the Chinese uses the same keys."""
    params = Reads(label.params)
    ({**TEXTS, **READ_TEXTS, **SCOPE_TEXTS}[label.code])(params)
    assert params.read == set(label.params)


def test_the_warnings_of_a_run_fill_into_whole_sentences(market_home, cache):
    run = service.backtest(service.BacktestRequest(saved={"id": "m"}, save=False), Context(market_home, cache))
    labels = {w.code: w for w in LABELS.warnings}
    sentences = [fill(labels[w.code], w.params) for w in run.warnings]
    assert run.warnings and all(s and "{" not in s for s in sentences)


def test_a_repeated_warning_fills_one_clause_per_item():
    label = next(w for w in LABELS.warnings if w.repeat is not None)
    params = {"index": "000300.SH", "days": 3, "first": "2024-01-02", "index_2": "000905.SH", "days_2": 4}
    sentence = fill(label, params | {"first_2": None})  # an empty member span has no first day
    assert sentence.count("000300.SH") == sentence.count("000905.SH") == 1 and label.repeat in sentence
    assert "2024-01-02" in sentence and "None" not in sentence


def test_a_job_stage_template_uses_exactly_the_params_that_stage_reports():
    assert {label.code: label.params for label in LABELS.job_stages if label.params} == STAGE_PARAMS
    stages = (*QUEUE_STAGES, *WORKER_STAGES)
    assert all("{" not in job_text(code, dict.fromkeys(STAGE_PARAMS.get(code, []), 1)) for code in stages)


def test_an_update_status_template_uses_only_the_params_an_update_result_gives():
    given = update_params({"days": [], "waiting": None, "proposed": 0, "unresolved": 0})
    assert all(set(label.params) <= set(given) for label in LABELS.update_statuses)


def test_an_order_cause_template_uses_exactly_the_params_that_cause_names():
    assert {label.code: set(label.params) for label in LABELS.order_causes} == {k: set(v) for k, v in PARAMS.items()}


def test_an_optional_clause_appears_only_when_its_param_is_set():
    label = next(w for w in LABELS.warnings if w.code == "data_anomaly")
    base = dict.fromkeys(label.params, 1)
    assert len(fill(label, base | {"max_share_day": None, "unavoidable": None})) < len(fill(label, base))
