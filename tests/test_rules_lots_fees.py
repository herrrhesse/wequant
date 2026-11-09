import datetime as dt

import numpy as np
import pytest

from wequant.rules.book import default_book
from wequant.rules.fees import Fees, trade_fees
from wequant.rules.lots import max_buy, max_sell

DAY = dt.date(2024, 2, 20)


def lot(board: str):
    return default_book().in_force("lot", board, DAY)


@pytest.mark.parametrize(
    "board, qty, expected",
    [
        ("main", 250, 200),
        ("main", 99, 0),
        ("chinext", 150, 100),
        ("star", 199, 0),
        ("star", 201, 201),
        ("bse", 99, 0),
        ("bse", 101, 101),
    ],
)
def test_max_buy(board, qty, expected):
    assert max_buy(lot(board), qty) == expected


@pytest.mark.parametrize(
    "board, qty, position, expected",
    [
        ("main", 240, 250, 200),
        ("main", 300, 250, 250),
        ("main", 1019.417, 1019.417, 1019.417),
        ("main", 1019, 1019.417, 1019),  # ten lots and the 19 odd whole shares; the fraction stays
        ("star", 150, 180, 0),
        ("star", 180, 180, 180),
        ("star", 240, 250, 240),
    ],
)
def test_max_sell(board, qty, position, expected):
    assert max_sell(lot(board), qty, position) == expected


def test_the_odd_remainder_sells_whole_in_one_order():
    assert max_sell(lot("main"), 50, 250) == 50
    assert max_sell(lot("main"), 160, 250) == 150  # one lot and the 50 odd shares
    assert max_sell(lot("main"), 1150, 1250) == 1150
    assert max_sell(lot("main"), 30, 250) == 0
    assert max_sell(lot("star"), 100, 250) == 0


def test_a_fraction_is_sold_only_with_the_full_position():
    assert max_sell(lot("main"), 50, 1019.417) == 19
    assert max_sell(lot("main"), 50.5, 250.5) == 50
    assert max_sell(lot("star"), 100, 250.5) == 0
    assert max_sell(lot("star"), 250.5, 250.5) == 250.5


def test_stamp_tax_tie_rounds_up_to_the_fen():
    fees = trade_fees(default_book(), "SZ", dt.date(2023, 8, 28), "sell", 101, 10000)
    assert fees == Fees(stamp_tax=510, transfer_fee=10) and fees.total == 520
    assert trade_fees(default_book(), "SZ", dt.date(2023, 8, 28), "sell", np.int64(101), np.int64(10000)) == fees


def test_fees_work_elementwise_on_arrays():
    shares, price = np.array([1000, 101], dtype=np.int64), np.array([10000, 10000], dtype=np.int64)
    fees = trade_fees(default_book(), "SZ", dt.date(2023, 8, 28), "sell", shares, price)
    assert fees.stamp_tax.tolist() == [5000, 510] and fees.transfer_fee.tolist() == [100, 10]
    assert fees.total.tolist() == [5100, 520]


@pytest.mark.parametrize("shares, price", [(1019.417, 10000), (1000, 10000.0), (np.array([1000.5]), 10000)])
def test_fees_reject_fractional_shares_and_prices(shares, price):
    with pytest.raises(TypeError):
        trade_fees(default_book(), "SH", DAY, "sell", shares, price)


def test_beijing_has_no_transfer_fee_rule():
    with pytest.raises(LookupError):
        trade_fees(default_book(), "BJ", DAY, "buy", 100, 10000)


def test_side_must_be_buy_or_sell():
    with pytest.raises(ValueError):
        trade_fees(default_book(), "SH", DAY, "short", 100, 10000)
