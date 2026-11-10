import json

import pytest

from wequant.service import CheckRequest, Context, Exit, IssuesRequest, ServiceError, data_check, data_issues


def issue(table: str, code: str, date: str, kind: str, action: str) -> str:
    return (
        f'{{"table":"{table}","code":"{code}","date":"{date}","kind":"{kind}","action":"{action}",'
        '"detail":null,"source":"test"}'
    )


def count(table: str, kind: str, action: str, rows: int) -> str:
    return f'{{"table":"{table}","kind":"{kind}","action":"{action}","rows":{rows}}}'


UNCONFIRMED = count("bars", "bar_unconfirmed", "row_dropped", 2)
DUPLICATE = count("bars", "duplicate_code", "duplicate_dropped", 1)
BREACH = count("bars", "limit_breach", "limits_voided", 1)
OFF_SCALE = count("bars", "off_scale", "row_dropped", 2)
BEFORE = count("daily_status", "before_first_bar", "status_dropped", 1)
D_10 = issue("bars", "000001.SZ", "2024-01-10", "bar_unconfirmed", "row_dropped")
B_10 = issue("bars", "300001.SZ", "2024-01-10", "bar_unconfirmed", "row_dropped")
B_11 = issue("bars", "300001.SZ", "2024-01-11", "limit_breach", "limits_voided")
A_04 = issue("bars", "600000.SH", "2024-01-04", "off_scale", "row_dropped")
A_05 = issue("bars", "600000.SH", "2024-01-05", "duplicate_code", "duplicate_dropped")
OFF_GRID = issue("bars", "600002.SH", "2024-01-04", "off_scale", "row_dropped")
B_02 = issue("daily_status", "300001.SZ", "2024-01-02", "before_first_bar", "status_dropped")


def issues_json(total: int, counts: list[str], rows: list[str]) -> str:
    return f'{{"data_version":1,"total":{total},"counts":[{",".join(counts)}],"rows":[{",".join(rows)}]}}'


@pytest.mark.parametrize(
    "request_, expected",
    [
        (
            IssuesRequest(),
            issues_json(
                7,
                [UNCONFIRMED, DUPLICATE, BREACH, OFF_SCALE, BEFORE],
                [D_10, B_10, B_11, A_04, A_05, OFF_GRID, B_02],
            ),
        ),
        (
            IssuesRequest(table="bars", limit=2),
            issues_json(6, [UNCONFIRMED, DUPLICATE, BREACH, OFF_SCALE], [D_10, B_10]),
        ),
        (
            IssuesRequest(codes=["300001.SZ"]),
            issues_json(
                3,
                [count("bars", "bar_unconfirmed", "row_dropped", 1), BREACH, BEFORE],
                [B_10, B_11, B_02],
            ),
        ),
    ],
)
def test_exact_json(context, request_, expected):
    assert data_issues(request_, context).model_dump_json() == expected


def test_filters(context):
    later = data_issues(IssuesRequest(start="2024-01-10"), context)
    assert [(r.code, r.date.isoformat()) for r in later.rows] == [
        ("000001.SZ", "2024-01-10"),
        ("300001.SZ", "2024-01-10"),
        ("300001.SZ", "2024-01-11"),
    ]
    off_scale = data_issues(IssuesRequest(kind="off_scale"), context)
    assert off_scale.total == 2 and [r.code for r in off_scale.rows] == ["600000.SH", "600002.SH"]
    none = data_issues(IssuesRequest(limit=0), context)
    assert none.rows == [] and none.total == 7 and len(none.counts) == 5
    inverted = data_issues(IssuesRequest(start="2024-01-10", end="2024-01-02"), context)
    assert inverted.total == 0 and inverted.counts == []


def test_a_recorded_breach_keeps_its_detail(limits_home):
    data_check(CheckRequest(commit=True), Context(limits_home))
    rows = data_issues(IssuesRequest(kind="limit_breach"), Context(limits_home)).rows
    assert [(r.code, r.date.isoformat()) for r in rows] == [("600001.SH", "2024-01-04")]
    assert json.loads(rows[0].detail)["high"] == 11500 and rows[0].source.startswith("limit_check@")


def test_no_such_version(context):
    with pytest.raises(ServiceError) as raised:
        data_issues(IssuesRequest(data_version=9), context)
    assert raised.value.exit == Exit.NOT_FOUND
