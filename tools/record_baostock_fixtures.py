"""Record small BaoStock responses as the offline fixtures of tests/test_baostock.py.

Run: uv run --extra baostock python -m tools.record_baostock_fixtures
"""

import json
from pathlib import Path

from wequant.data.sources.baostock import DAILY_FIELDS, FIRST_DAY, BaoStock, from_baostock

OUT = Path(__file__).resolve().parents[1] / "tests" / "data" / "baostock.json"


def daily(code: str, start: str, end: str) -> tuple[str, dict]:
    params = {
        "code": code,
        "fields": DAILY_FIELDS,
        "start_date": start,
        "end_date": end,
        "frequency": "d",
        "adjustflag": "3",
    }
    return "query_history_k_data_plus", params


CASES = {
    "daily_traded": daily("sh.600000", "2026-11-02", "2026-11-06"),
    "daily_suspended": daily("sz.300273", "2022-04-11", "2022-05-06"),
    "daily_st": daily("sh.600337", "2026-04-20", "2026-05-15"),
    "stock_basic": ("query_stock_basic", {}),
    "all_stock": ("query_all_stock", {"day": "2026-11-06"}),
    "trade_dates": ("query_trade_dates", {"start_date": "2026-10-26", "end_date": "2026-11-08"}),
    "adjust_factor": ("query_adjust_factor", {"code": "sh.600519", "start_date": FIRST_DAY, "end_date": "2026-11-08"}),
    "dividend": ("query_dividend_data", {"code": "sh.600519", "year": "2025", "yearType": "operate"}),
}


def trim(name: str, fields: list[str], rows: list[list[str]]) -> list[list[str]]:
    """Keep fixtures small: a mix of listed, delisted and non-stock rows, or the first rows."""
    if name == "stock_basic":
        status, kind = fields.index("status"), fields.index("type")
        listed = [r for r in rows if r[kind] == "1" and r[status] == "1"][:8]
        delisted = [r for r in rows if r[kind] == "1" and r[status] == "0"][:3]
        other = [r for r in rows if r[kind] != "1"][:3]
        return listed + delisted + other
    return rows[:20] if name == "all_stock" else rows


def main() -> None:
    recorded = {}
    with BaoStock(retries=0) as source:
        for name, (method, params) in CASES.items():
            fields, rows = source.query(method, **params)
            case = {"method": method, "params": params, "fields": fields, "rows": trim(name, fields, rows)}
            if method == "query_history_k_data_plus":
                case |= {
                    "code": from_baostock(params["code"]),
                    "start": params["start_date"],
                    "end": params["end_date"],
                }
            recorded[name] = case
        requests = source.requests
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(recorded, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"{len(recorded)} fixtures, {requests} requests -> {OUT}")


if __name__ == "__main__":
    main()
