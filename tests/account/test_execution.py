"""Execution: validation, the exact rates and the fen rounding of commission and slippage."""

import math

import numpy as np
import pytest

from wequant.account import Execution


@pytest.mark.parametrize(
    "fields",
    [
        {"price": "vwap"},
        {"price": None},
        {"commission_rate": -0.1},
        {"commission_rate": 1.0},
        {"commission_rate": True},
        {"commission_rate": np.True_},
        {"commission_rate": math.nan},
        {"commission_rate": "0.0003"},
        {"min_commission": -1},
        {"min_commission": 5.001},
        {"min_commission": math.inf},
        {"slippage_bps": -1},
        {"slippage_bps": 10000},
    ],
)
def test_bad_fields_raise(fields):
    with pytest.raises(ValueError):
        Execution(**fields)


def test_to_dict_round_trips():
    for execution in (Execution(), Execution("close", 0.00025, 0, 7.5)):
        assert Execution(**execution.to_dict()) == execution


def test_commission_is_the_rate_half_up_to_the_fen_but_at_least_the_minimum():
    execution = Execution()
    assert execution.commission(100_000_000) == 30_000
    assert execution.commission(2_400_000) == 5_000
    assert execution.commission(33_350_000) == 10_010  # 10.005 yuan
    assert Execution(min_commission=0).commission(16_000) == 0  # 4.8 厘 is under half a fen
    assert Execution(min_commission=0).commission(16_667) == 10


def test_slippage_is_half_up_to_the_fen():
    assert Execution(slippage_bps=10).slippage(10_000_000) == 10_000
    assert Execution(slippage_bps=2.5).slippage(1_234_567) == 310  # 308.64 厘 is 30.864 fen
    assert Execution(slippage_bps=1).slippage(49_999) == 0
    assert Execution(slippage_bps=1).slippage(50_000) == 10  # half a fen rounds up
    assert Execution().slippage(5) == 0


def test_the_default_slippage_is_ten_basis_points_half_up_to_the_fen():
    assert Execution().slippage_bps == 10 and Execution().to_dict()["slippage_bps"] == 10.0
    assert Execution().slippage(12_345_000) == 12_350  # 12.345 yuan
    assert Execution().slippage(12_333_000) == 12_330  # 12.333 yuan
    assert Execution(slippage_bps=0).slippage(12_345_000) == 0


def test_numpy_scalars_are_kept_as_floats():
    execution = Execution(commission_rate=np.float64(0.0003), min_commission=np.float64(5.0), slippage_bps=np.int64(10))
    assert execution == Execution()
    assert all(type(value) is float for value in execution.to_dict().values() if not isinstance(value, str))
    assert execution.commission(100_000_000) == 30_000
