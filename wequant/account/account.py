"""Cash and positions of one run, moved one trading day at a time, through the one order validation."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from wequant.account import ledger
from wequant.account.execution import EXECUTION_VERSION, FEN, LI, Execution, exact, finite_number
from wequant.account.ledger import FILLED, ORDER_REASONS, PARTIAL, REJECTED, Fills, Ledger
from wequant.rules import default_book, max_buy, max_sell, trade_fees
from wequant.version import code_version

if TYPE_CHECKING:
    from numpy.typing import ArrayLike

    from wequant.panel.panel import Day, Panel

NO_POSITION, NO_BAR, LIMIT_UNKNOWN, LIMIT_UP, LIMIT_DOWN, T1, LOT, CASH = range(len(ORDER_REASONS))
NO_COSTS = (0, 0, 0, 0)  # commission, stamp tax, transfer fee, slippage in 厘


class Account:
    """Cash and positions of one run on a panel: begin(t), trade(...) any number of times, end()."""

    def __init__(self, panel: Panel, cash: float, start: Day, execution: Execution = Execution()) -> None:
        """Hold cash yuan and nothing else at the close of trading day start, the base day."""
        self.t, self.execution, self._panel = panel.row(start), execution, panel
        self._cash = _whole_fen(cash)
        self._equity = self._cash / LI
        self._price, self._close, self._factor = panel.raw(execution.price), panel.raw("close"), panel.factor
        self._traded, self._known = panel.traded, panel.limit_known
        self._up, self._down = panel.limit_up_price, panel.limit_down_price
        self._boards, self._exchanges = panel.boards.tolist(), panel.exchanges.tolist()
        self._book, self._open = default_book(), False
        n = len(panel.codes)
        self._held, self._bought = np.zeros(n), np.zeros(n)
        self._last_close, self._last_factor = np.full(n, np.nan), np.full(n, np.nan)
        self._days = [(self.t, self._equity, self._equity, 0.0, 0.0)]
        self._holdings: list[tuple] = []
        self._batches: list[tuple] = []

    @property
    def cash(self) -> float:
        """Cash in yuan, now."""
        return self._cash / LI

    @property
    def shares(self) -> np.ndarray:
        """(N,) shares held, read-only; fractional after ex-dates."""
        return _frozen(self._held + self._bought)

    @property
    def sellable(self) -> np.ndarray:
        """(N,) shares that may be sold now: held at the open less today's sales (T+1), read-only."""
        return _frozen(self._held.copy())

    @property
    def equity(self) -> float:
        """Equity at the last close: what the last end() returned, or the initial cash before it; begin and trade leave it unchanged."""
        return self._equity

    @property
    def values(self) -> np.ndarray:
        """(N,) yuan of each holding at the last close, rescaled by D3 on the open day, read-only; NaN for shares bought today of a code not yet closed in the run."""
        shares = self._held + self._bought
        return _frozen(np.where(shares > 0, shares * self._mark(), 0.0))

    def begin(self, t: int) -> None:
        """Open row t, the day after the last: scale holdings by factor[t] / factor[t-1]; all of them become sellable."""
        if self._open or t != self.t + 1:
            raise RuntimeError(f"cannot open row {t}: " + ("a day is open" if self._open else f"the last is {self.t}"))
        self._held = (self._held + self._bought) * (self._factor[t] / self._factor[t - 1])
        self._bought = np.zeros_like(self._held)
        self.t, self._open, self._day = t, True, self._panel.dates[t].item()
        self._day_bought = self._day_sold = 0
        self._rules = {}
        self._row = (self._price[t], self._traded[t], self._known[t], self._up[t], self._down[t])

    def trade(self, cols: ArrayLike, qty: ArrayLike) -> Fills:
        """Validate and fill one batch at day t's price, every sell before every buy; qty > 0 buys, < 0 sells, -inf asks for the whole position."""
        self._require_open()
        cols, qty = self._batch(cols, qty)
        for j in cols[self._traded[self.t, cols]]:  # every rule a fill needs, before anything fills
            self._rule("lot", self._boards[j])
            self._rule("stamp_tax", self._exchanges[j])
            self._rule("transfer_fee", self._exchanges[j])
        results = [()] * len(cols)
        for k in np.concatenate([np.flatnonzero(qty < 0), np.flatnonzero(qty > 0)]).tolist():
            results[k] = self._order(int(cols[k]), float(qty[k]))
        got = ledger.fills(results)
        self._batches.append((self.t, cols, qty, got))
        return got

    def end(self) -> float:
        """Mark holdings at day t's raw close, or the last traded close × factor ratio without a bar; returns equity."""
        self._require_open()
        traded = self._traded[self.t]
        self._last_close = np.where(traded, self._close[self.t], self._last_close)
        self._last_factor = np.where(traded, self._factor[self.t], self._last_factor)
        shares = self._held + self._bought
        held = np.flatnonzero(shares > 0)
        values = shares[held] * self._mark()[held]
        self._equity = self._cash / LI + float(values.sum())
        self._days.append((self.t, self._equity, self._cash / LI, self._day_bought / LI, self._day_sold / LI))
        self._holdings.append((self.t, held, shares[held], values))
        self._open = False
        return self._equity

    def ledger(self) -> Ledger:
        """The closed days as analytics frames, with the run's identities."""
        if self._open:
            raise RuntimeError("a day is open: end() it before taking the ledger")
        panel = self._panel
        meta = {
            "store_version": panel.version,
            "data_key": panel.key,
            "rules_key": panel.rules_key,
            "code_version": code_version(),
            "execution": self.execution.to_dict(),
            "execution_version": EXECUTION_VERSION,
            "cash": self._days[0][2],
            "start": panel.dates[self._days[0][0]].item().isoformat(),
        }
        return Ledger(
            ledger.daily_frame(self._days, panel.dates),
            ledger.positions_frame(self._holdings, panel.dates, panel.codes),
            ledger.orders_frame(self._batches, panel.dates, panel.codes),
            meta,
        )

    # ------------------------------------------------------------ one order

    def _order(self, j: int, q: float) -> tuple:
        """The fill of one order of column j, q shares (< 0 sells), as a ledger.fills row."""
        price, traded, known, up, down = (part[j] for part in self._row)
        sell = q < 0
        p = float(price) if traded and price > 0 else math.nan
        if sell and self._held[j] + self._bought[j] <= 0:
            return _rejected(NO_POSITION, p)
        if math.isnan(p):
            return _rejected(NO_BAR, p)
        if not known:
            return _rejected(LIMIT_UNKNOWN, p)
        if sell and p <= down or not sell and p >= up:
            return _rejected(LIMIT_DOWN if sell else LIMIT_UP, p)
        lot, li = self._rule("lot", self._boards[j]), int(np.rint(p * LI))
        return self._sell(j, -q, p, li, lot) if sell else self._buy(j, q, p, li, lot)

    def _sell(self, j: int, q: float, p: float, li: int, lot) -> tuple:
        held = float(self._held[j])
        if held <= 0:
            return _rejected(T1, p)
        got = max_sell(lot, min(q, held), held)
        if got == 0:
            return _rejected(LOT, p)
        whole = math.floor(got)
        costs = self._costs("sell", j, whole, li)
        value = whole * li + math.floor((got - whole) * li / FEN + 0.5) * FEN
        if self._cash + value - sum(costs) < 0:
            return _rejected(CASH, p)
        self._cash += value - sum(costs)
        self._held[j] = 0.0 if got == held else held - got
        self._day_sold += value
        short = q > held and self._bought[j] > 0
        return (got, p, value, *costs, PARTIAL, T1) if short else (got, p, value, *costs, FILLED, -1)

    def _buy(self, j: int, q: float, p: float, li: int, lot) -> tuple:
        want = max_buy(lot, math.floor(q))
        if want == 0:
            return _rejected(LOT, p)
        got, costs = self._affordable(j, li, lot, want)
        if got == 0:
            return _rejected(CASH, p)
        value = got * li
        self._cash -= value + sum(costs)
        self._bought[j] += got
        self._day_bought += value
        return (got, p, value, *costs, FILLED, -1) if got == want else (got, p, value, *costs, PARTIAL, CASH)

    def _affordable(self, j: int, li: int, lot, want: int) -> tuple[int, tuple]:
        """The largest valid buy size up to want that cash pays for with its costs, and those costs; want first."""
        if (costs := self._payable(j, want, li)) is not None:
            return want, costs
        top = max_buy(lot, min(want, self._cash // li))
        best, lo, hi = (0, NO_COSTS), -1, (top - lot.min_qty) // lot.step if top else -1
        while lo < hi:  # cost grows with size: lot steps up to lo are payable, steps above hi are not
            k = (lo + hi + 1) // 2
            size = lot.min_qty + k * lot.step
            if (costs := self._payable(j, size, li)) is not None:
                best, lo = (size, costs), k
            else:
                hi = k - 1
        return best

    def _payable(self, j: int, size: int, li: int) -> tuple[int, int, int, int] | None:
        """The costs of buying size shares at li 厘 when cash pays for the shares and the costs, else None."""
        if size * li > self._cash:
            return None
        costs = self._costs("buy", j, size, li)
        return costs if size * li + sum(costs) <= self._cash else None

    def _costs(self, side: str, j: int, whole: int, li: int) -> tuple[int, int, int, int]:
        """Commission, stamp tax, transfer fee and slippage in 厘 of whole shares at li 厘; all 0 for none."""
        if whole == 0:
            return NO_COSTS
        value = whole * li
        fees = trade_fees(self._book, self._exchanges[j], self._day, side, whole, li)
        return self.execution.commission(value), fees.stamp_tax, fees.transfer_fee, self.execution.slippage(value)

    # ------------------------------------------------------------ helpers

    def _rule(self, table: str, key: str):
        """The rule of table for key on the open day, looked up once a day."""
        if (table, key) not in self._rules:
            self._rules[table, key] = self._book.in_force(table, key, self._day)
        return self._rules[table, key]

    def _mark(self) -> np.ndarray:
        """(N,) the last traded close in row t's adjustment: shares held now times it is their value."""
        return self._last_close * (self._last_factor / self._factor[self.t])

    def _batch(self, cols: ArrayLike, qty: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
        cols, qty = np.asarray(cols), np.asarray(qty, dtype=np.float64)
        cols = cols.astype(np.int64) if cols.size == 0 else cols
        if cols.ndim != 1 or not np.issubdtype(cols.dtype, np.integer):
            raise IndexError(f"columns must be a 1-D sequence of integers, not {cols.dtype} of shape {cols.shape}")
        if cols.size and (cols.min() < 0 or cols.max() >= len(self._boards)):
            raise IndexError(f"a column is outside [0, {len(self._boards)})")
        if qty.shape != cols.shape:
            raise ValueError(f"{qty.size} quantities for {cols.size} columns")
        if np.any((qty == 0) | np.isnan(qty) | (qty == np.inf)):
            raise ValueError("a quantity is 0, NaN or +inf")
        return cols, qty

    def _require_open(self) -> None:
        if not self._open:
            raise RuntimeError("no day is open: call begin(t) first")


def _rejected(reason: int, p: float) -> tuple:
    return (0.0, p, 0, *NO_COSTS, REJECTED, reason)


def _whole_fen(cash: float) -> int:
    """cash yuan in 厘; ValueError unless it is a positive, finite whole number of fen."""
    li = exact(finite_number("cash", cash)) * LI
    if li <= 0 or li % FEN:
        raise ValueError(f"cash {cash!r} is not a positive whole number of fen")
    return int(li)


def _frozen(array: np.ndarray) -> np.ndarray:
    array.flags.writeable = False
    return array
