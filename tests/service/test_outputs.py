"""Result parts several operations share."""

import datetime as dt

import polars as pl

from wequant.backtest.anomalies import ANOMALY_USES
from wequant.service.outputs import data_anomaly

DAY = dt.date(2024, 1, 2)


def uses(n):
    row = {"date": DAY, "channel": "signal", "anomaly_date": DAY, "kind": "price_mismatch", "class": "price"}
    row |= {"level": "error", "root": "f", "full_history": False, "decision": "target", "seq": 0}
    return pl.DataFrame([{**row, "code": f"{600000 + i}.SH"} for i in range(n)], schema=ANOMALY_USES)


def test_a_result_samples_its_first_20_uses_and_a_screen_all_of_them():
    found = data_anomaly(uses(25), ())
    assert (len(found.sample), found.cells, found.kinds, found.levels.error) == (20, 25, {"price_mismatch": 25}, 25)
    assert len(data_anomaly(uses(25), (), sample=None).sample) == 25 and data_anomaly(uses(0), ()) is None
