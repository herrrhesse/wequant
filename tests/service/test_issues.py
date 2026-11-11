from wequant.data.store import Store
from wequant.service import CheckRequest, Context, IssuesRequest, data_check, data_issues


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


def issues_json(name: str, total: int, counts: list[str], rows: list[str]) -> str:
    head = f'{{"data_version":1,"data_version_name":"{name}","total":{total}'
    return f'{head},"counts":[{",".join(counts)}],"rows":[{",".join(rows)}]}}'


def test_exact_json(context):
    """The wire format of data issues: total and counts over every matching row, the rows in store order."""
    day = Store(context.home).manifest()["created_at"][:10].replace("-", "")  # no last trade date: its creation day
    expected = issues_json(
        f"{day}.1",
        7,
        [UNCONFIRMED, DUPLICATE, BREACH, OFF_SCALE, BEFORE],
        [D_10, B_10, B_11, A_04, A_05, OFF_GRID, B_02],
    )
    assert data_issues(IssuesRequest(), context).model_dump_json() == expected


def test_filters(context):
    """Each filter narrows the rows, total and counts; limit only pages the rows."""

    def found(**request):
        result = data_issues(IssuesRequest(**request), context)
        return result.total, len(result.counts), [(r.code, r.date.isoformat()) for r in result.rows]

    assert found(table="bars", limit=2) == (6, 4, [("000001.SZ", "2024-01-10"), ("300001.SZ", "2024-01-10")])
    assert found(codes=["300001.SZ"]) == (
        3,
        3,
        [("300001.SZ", "2024-01-10"), ("300001.SZ", "2024-01-11"), ("300001.SZ", "2024-01-02")],
    )
    assert found(start="2024-01-10") == (
        3,
        2,
        [("000001.SZ", "2024-01-10"), ("300001.SZ", "2024-01-10"), ("300001.SZ", "2024-01-11")],
    )
    assert found(kind="off_scale") == (2, 1, [("600000.SH", "2024-01-04"), ("600002.SH", "2024-01-04")])
    assert found(limit=0) == (7, 5, [])
    assert found(start="2024-01-10", end="2024-01-02") == (0, 0, [])


def test_a_pinned_version_reads_its_own_issues(limits_home):
    """data_version reads that version's data_issues: the breach v2 records is not in v1."""
    data_check(CheckRequest(commit=True), Context(limits_home))
    assert data_issues(IssuesRequest(kind="limit_breach"), Context(limits_home)).total == 1
    assert data_issues(IssuesRequest(kind="limit_breach", data_version=1), Context(limits_home)).total == 0
