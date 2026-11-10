"""The detail frame on a hand-made decision with holdings: every action, and every sell reason in its order."""

from types import SimpleNamespace

import numpy as np

from wequant.expr import Field
from wequant.strategy import Condition, Factor, Pool, Strategy, choose, cross_section, detail_frame, stack, verdicts

NAN = np.nan
CODES = [f"60000{k}.SH" for k in range(10)]
STRATEGY = Strategy(
    filters=(Condition("ok", Field("close") > 1.0),),
    factors=(Factor("x", Field("close")),),
    top_n=1,
    hold_rank=2,
    pool=Pool(exclude_st=True),
)
#            0    1    2    3    4    5    6    7    8    9
UNIVERSE = [0, 1, 0, 1, 1, 1, 1, 1, NAN, 1]
NOT_ST = [1, 0, 0, 1, 1, 1, 1, 1, NAN, 1]
OK = [1, 1, 1, 0, 1, 1, 1, 1, NAN, 1]
X = [9, 9, 9, 9, NAN, 3, 2, 1, NAN, 0.5]
HELD = [True] * 9 + [False]


def test_every_action_and_sell_reason():
    grids = {"universe": UNIVERSE, "exclude_st": NOT_ST, "ok": OK}
    grids = {name: verdicts(np.array([v], dtype=float)) for name, v in grids.items()} | {"x": np.array([X])}
    traded = np.array([[not np.isnan(u) for u in UNIVERSE]])
    section = cross_section(STRATEGY, grids, np.array([0]), np.ones((1, 10), dtype=bool), traded)
    choice = stack([choose(STRATEGY, section, 0, np.array(HELD))])
    panel = SimpleNamespace(dates=np.array(["2024-01-02"], dtype="datetime64[D]"), codes=np.array(CODES))
    detail = detail_frame(STRATEGY, section, choice, panel)
    columns = ("code", "rank", "held", "action", "sell_kind", "sell_step", "sell_rank", "sell_limit")
    assert detail.select(columns).rows() == [
        (CODES[0], None, True, "sell", "universe", None, None, None),  # only the universe expression failed
        (CODES[1], None, True, "sell", "universe", "exclude_st", None, None),
        (CODES[2], None, True, "sell", "universe", "exclude_st", None, None),  # the pool item that failed is named
        (CODES[3], None, True, "sell", "filter", "ok", None, None),
        (CODES[4], None, True, "sell", "no_score", "x", None, None),
        (CODES[5], 1, True, "keep", None, None, None, None),
        (CODES[6], 2, True, "sell", "slots", None, 2, 1),  # within hold_rank, but the one slot went to rank 1
        (CODES[7], 3, True, "sell", "rank", None, 3, 2),
        (CODES[8], None, True, "wait", None, None, None, None),  # no bar: no decision
        (CODES[9], 4, False, "none", None, None, None, None),
    ]
    assert detail["universe"].to_list() == [False, True, False, True, True, True, True, True, None, True]
