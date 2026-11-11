"""data bars: panel fields of a few codes over a date range, as an expression reads them."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from wequant.expr import FIELDS
from wequant.service.base import Context, Distinct, Model, PanelRequest, named, open_panel
from wequant.service.rows import Row, rows

BarField = Literal[tuple(FIELDS)]
DEFAULT_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "adj_close",
    "limit_up_price",
    "limit_down_price",
)


class BarsRequest(PanelRequest):
    """Panel fields of a few codes over a date range."""

    fields: Annotated[list[BarField], Distinct] = Field(list(DEFAULT_FIELDS), min_length=1)
    """Panel fields, in column order."""


class Bars(Model):
    """Each cell's values: prices and amount in yuan, volume in shares, adj_* back-adjusted, masks 1.0/0.0."""

    data_version: int
    data_version_name: str
    """The version's display name, YYYYMMDD.N: its last trade date, then which version of that day."""
    fields: list[BarField]
    rows: list[Row]


def data_bars(request: BarsRequest, context: Context = Context()) -> Bars:
    """The panel's values for the request's codes and days, as an expression reads them."""
    panel = open_panel(request, context)
    grids = {name: FIELDS[name].load(panel) for name in request.fields}
    return Bars.model_validate({**named(panel.manifest), "fields": request.fields, "rows": rows(panel, grids)})
