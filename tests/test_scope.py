"""A data root's scope in layers (S2.11): a wider scope that reaches back to the next layer's first day becomes one
layer with it, and what the layers cover from which day."""

import datetime as dt

from wequant.data.scope import ALL, Layer, Scope, covers, widen

D1, D2, D3 = dt.date(2023, 11, 15), dt.date(2025, 1, 2), dt.date(2026, 1, 5)
CSI300, BOTH = Scope(indexes=("000300.SH",)), Scope(indexes=("000300.SH", "000905.SH"))


def test_a_wider_layer_reaching_the_next_layer_s_first_day_becomes_one_layer_with_it():
    assert widen([Layer(BOTH, D3), Layer(CSI300, D1)], BOTH, D2) == [Layer(BOTH, D2), Layer(CSI300, D1)]
    assert widen([Layer(BOTH, D2), Layer(CSI300, D1)], BOTH, D1) == [Layer(BOTH, D1)]


def test_each_strategy_scope_is_covered_from_the_first_day_of_the_oldest_layer_holding_it():
    held = [Layer(ALL, D3), Layer(BOTH, D2), Layer(CSI300, D1)]
    assert covers(held) == {
        "all": D3,
        "000300.SH": D1,
        "000905.SH": D2,
        "000906.SH": D3,
        "000852.SH": D3,
        "000985.SH": D3,
    }
