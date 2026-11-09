import tomllib
from pathlib import Path

import pytest

from tests.test_rules_limits import inputs
from wequant.rules.book import default_book
from wequant.rules.fees import trade_fees
from wequant.rules.limits import FREE, LIMITED, UNKNOWN, price_limits
from wequant.rules.lots import max_buy, max_sell

EXAMPLES = tomllib.loads((Path(__file__).parent / "data" / "rule_examples.toml").read_text("utf-8"))
STATES = {"LIMITED": LIMITED, "FREE": FREE, "UNKNOWN": UNKNOWN}


def named(example: dict) -> str:
    return f"{example.get('code') or example.get('exchange') or example['board']} {example['date']}"


def test_every_rule_has_an_example():
    limits = {e["limit_rule"] for e in EXAMPLES["limit"]}
    lots = {e["rule"] for e in EXAMPLES["lot"]}
    fees = {e[key] for e in EXAMPLES["fee"] for key in ("stamp_rule", "transfer_rule")}
    assert {rule.id for rule in default_book().rules()} <= limits | lots | fees


@pytest.mark.parametrize("example", EXAMPLES["limit"], ids=named)
def test_limit_examples(example):
    out = price_limits(inputs([example])).row(0, named=True)
    expected = (example.get("limit_up"), example.get("limit_down"), example["limit_rule"])
    assert (out["limit_up"], out["limit_down"], out["limit_rule"]) == expected, example["source"]
    assert out["limit_state"] == STATES[example["limit_state"]]
    if out["limit_state"] == LIMITED:
        assert out["limit_down"] <= example["low"] <= example["high"] <= out["limit_up"]


@pytest.mark.parametrize("example", EXAMPLES["lot"], ids=named)
def test_lot_examples(example):
    lot = default_book().in_force("lot", example["board"], example["date"])
    assert lot.id == example["rule"]
    if example["side"] == "buy":
        assert max_buy(lot, example["qty"]) == example["expected"]
    else:
        assert max_sell(lot, example["qty"], example["position"]) == example["expected"]


@pytest.mark.parametrize("example", EXAMPLES["fee"], ids=named)
def test_fee_examples(example):
    book, exchange, day = default_book(), example["exchange"], example["date"]
    assert book.in_force("stamp_tax", exchange, day).id == example["stamp_rule"]
    assert book.in_force("transfer_fee", exchange, day).id == example["transfer_rule"]
    fees = trade_fees(book, exchange, day, example["side"], example["shares"], example["price"])
    assert (fees.stamp_tax, fees.transfer_fee) == (example["stamp_tax"], example["transfer_fee"]), example["source"]
