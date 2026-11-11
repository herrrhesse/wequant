"""data calendar and data securities (F-27) on tests.test_panel's store."""

import datetime as dt

from tests.test_panel import TABLE_ROWS, seed
from wequant import service
from wequant.service import Context


def test_the_calendar_lists_the_days_in_range_and_the_version_s_first_and_last(home):
    found = service.data_calendar(service.CalendarRequest(start="2024-01-04", end="2024-01-08"), Context(home))
    assert (found.first, found.last) == (dt.date(2024, 1, 2), dt.date(2024, 1, 11))
    assert found.dates == [dt.date(2024, 1, 4), dt.date(2024, 1, 5), dt.date(2024, 1, 8)]


def test_securities_match_a_code_prefix_in_any_case_or_part_of_a_name(tmp_path):
    names = ["浦发银行", "示例", "北证", "平安银行"]  # A 600000.SH, B 300001.SZ, D 000001.SZ, C 830001.BJ
    named = [row | {"name": name} for row, name in zip(TABLE_ROWS["securities"], names, strict=True)]
    context = Context(seed(tmp_path / "home", securities=named).home)

    def codes(query: str) -> list[str]:
        return [r.code for r in service.data_securities(service.SecuritiesRequest(query=query), context).rows]

    assert (codes("600"), codes("000001.sz"), codes("银行")) == (
        ["600000.SH"],
        ["000001.SZ"],
        ["600000.SH", "830001.BJ"],
    )
    page = service.data_securities(service.SecuritiesRequest(limit=2), context)
    assert (page.total, [r.code for r in page.rows]) == (4, ["000001.SZ", "300001.SZ"])
