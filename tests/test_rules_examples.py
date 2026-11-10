import tomllib
from pathlib import Path

import pytest

from tests.test_rules_limits import inputs
from tests.test_rules_lots_fees import DAY, MAX_BUY
from wequant.rules.book import default_book
from wequant.rules.fees import trade_fees
from wequant.rules.limits import FREE, LIMITED, UNKNOWN, price_limits

EXAMPLES = tomllib.loads((Path(__file__).parent / "data" / "rule_examples.toml").read_text("utf-8"))
STATES = {"LIMITED": LIMITED, "FREE": FREE, "UNKNOWN": UNKNOWN}


def named(example: dict) -> str:
    return example["case"]


def test_every_rule_has_an_example():
    limits = {e["limit_rule"] for e in EXAMPLES["limit"]}
    fees = {e[key] for e in EXAMPLES["fee"] for key in ("stamp_rule", "transfer_rule")}
    lots = {default_book().in_force("lot", board, DAY).id for board, _, _ in MAX_BUY}
    assert {rule.id for rule in default_book().rules()} <= limits | fees | lots


@pytest.mark.parametrize("example", EXAMPLES["limit"], ids=named)
def test_limit_examples(example):
    out = price_limits(inputs([example])).row(0, named=True)
    expected = (example.get("limit_up"), example.get("limit_down"), example["limit_rule"])
    assert (out["limit_up"], out["limit_down"], out["limit_rule"]) == expected, example["why"]
    assert out["limit_state"] == STATES[example["limit_state"]]


@pytest.mark.parametrize("example", EXAMPLES["fee"], ids=named)
def test_fee_examples(example):
    book, exchange, day = default_book(), example["exchange"], example["date"]
    assert book.in_force("stamp_tax", exchange, day).id == example["stamp_rule"]
    assert book.in_force("transfer_fee", exchange, day).id == example["transfer_rule"]
    fees = trade_fees(book, exchange, day, example["side"], example["shares"], example["price"])
    assert (fees.stamp_tax, fees.transfer_fee) == (example["stamp_tax"], example["transfer_fee"]), example["why"]
