"""wequant's operations: one function each, pydantic models in and out; the CLI and the API only call these."""

from wequant.service.bars import Bars, BarsRequest, data_bars
from wequant.service.base import Check, Context, ErrorBody, ErrorDetail
from wequant.service.check import CheckReport, CheckRequest, data_check
from wequant.service.contract import OperationModels, SchemaRequest, ServiceSchema, schema
from wequant.service.errors import Exit, ServiceError
from wequant.service.expr import EvalRequest, EvalResult, ExprCheck, ExprRequest, expr_check, expr_eval
from wequant.service.issues import IssueCount, IssueRow, Issues, IssuesRequest, data_issues
from wequant.service.rows import Row
from wequant.service.status import DataStatus, StatusRequest, TableStatus, data_status

__all__ = [
    "Bars",
    "BarsRequest",
    "Check",
    "CheckReport",
    "CheckRequest",
    "Context",
    "DataStatus",
    "EvalRequest",
    "EvalResult",
    "Exit",
    "ExprCheck",
    "ExprRequest",
    "ErrorBody",
    "ErrorDetail",
    "IssueCount",
    "IssueRow",
    "Issues",
    "IssuesRequest",
    "OperationModels",
    "Row",
    "SchemaRequest",
    "ServiceError",
    "ServiceSchema",
    "StatusRequest",
    "TableStatus",
    "data_bars",
    "data_check",
    "data_issues",
    "data_status",
    "expr_check",
    "expr_eval",
    "schema",
]
