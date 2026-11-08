"""Turn an extracted release into wequant tables.

Upstream normalizes each instrument by its first adjusted close: prices are raw × factor,
volume is raw / factor, while amount and adjclose stay raw. Raw volume is in 手 and raw
amount in 千元.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from wequant.data import codes as codemod
from tools.investment_data import qlib
from wequant.data.schema import TABLES
from tools.investment_data import adjustment, checks

FIELDS = ("open", "high", "low", "close", "volume", "amount", "adjclose", "vwap", "factor", "change")
PRICES = ("open", "high", "low", "close")

# instruments/<name>.txt -> index code, as mapped in upstream qlib/dump_index_weight.py.
INDEX_LISTS = {
    "csi300": "399300.SZ",
    "csi500": "000905.SH",
    "csi800": "000906.SH",
    "csi1000": "000852.SH",
    "csiall": "000985.SH",
}


class ConventionError(RuntimeError):
    """The data breaks a convention the conversion relies on; nothing should be written."""

    def __init__(self, problems: list[str], stats: dict):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.stats = stats


@dataclass
class Recovered:
    """Rows that have data, with raw values: yuan (index points), 手 and 千元."""

    codes: list[str]  # wequant code per instrument
    kinds: np.ndarray  # stock / index / fund / unknown per instrument
    spans: list[qlib.Span]
    calendar: np.ndarray
    code_idx: np.ndarray
    dates: np.ndarray
    price: dict[str, np.ndarray]  # open, high, low, close, vwap
    lots: np.ndarray
    amount_k: np.ndarray
    adjclose: np.ndarray  # float32, as stored
    stored_close: np.ndarray  # normalized close, as stored

    @property
    def kind(self) -> np.ndarray:
        return self.kinds[self.code_idx]


@dataclass
class Stocks:
    """Stock rows of a Recovered, with the close rounded to whole fen."""

    rows: np.ndarray  # indices into Recovered rows
    code_idx: np.ndarray
    dates: np.ndarray
    close: np.ndarray
    adjclose: np.ndarray


def classify(qlib_code: str) -> tuple[str, str]:
    """(wequant code, stock / index / fund / unknown)."""
    try:
        code = qlib.to_wequant(qlib_code)
    except ValueError:
        return qlib_code, "unknown"
    try:
        return code, codemod.security_type(code)
    except ValueError:
        return code, "unknown"


def recover(panel: qlib.Panel) -> Recovered:
    rows = np.flatnonzero(np.isfinite(panel.values["close"]))
    v = {f: panel.values[f][rows].astype(np.float64) for f in FIELDS}
    codes, kinds = zip(*(classify(s.code) for s in panel.spans))
    return Recovered(
        codes=list(codes),
        kinds=np.array(kinds),
        spans=panel.spans,
        calendar=panel.calendar,
        code_idx=panel.code_idx[rows],
        dates=panel.calendar[panel.cal_idx[rows]],
        price={f: v[f] / v["factor"] for f in (*PRICES, "vwap")},
        lots=v["volume"] * v["factor"],
        amount_k=v["amount"],
        adjclose=panel.values["adjclose"][rows],
        stored_close=v["close"],
    )


def stocks_of(rec: Recovered) -> Stocks:
    rows = np.flatnonzero(rec.kind == "stock")
    return Stocks(
        rows=rows,
        code_idx=rec.code_idx[rows],
        dates=rec.dates[rows],
        close=np.round(rec.price["close"][rows], 2),
        adjclose=rec.adjclose[rows],
    )


def read_index_members(root: Path) -> tuple[pl.DataFrame, list[tuple], list[str]]:
    """index_members rows, spans whose code is malformed, and lists with no known index code."""
    frames, invalid, unmapped = [], [], []
    for path in sorted((root / "instruments").glob("*.txt")):
        if path.stem == "all":
            continue
        if path.stem not in INDEX_LISTS:
            unmapped.append(path.name)
            continue
        members = []
        for span in qlib.read_instruments(path):
            try:
                members.append((qlib.to_wequant(span.code), span.start, span.end))
            except ValueError:
                invalid.append((path.name, span.code, str(span.start), str(span.end)))
        frames.append(
            pl.DataFrame(
                {
                    "index_code": [INDEX_LISTS[path.stem]] * len(members),
                    "code": [m[0] for m in members],
                    "start_date": _dates([m[1] for m in members]),
                    "end_date": _dates([m[2] for m in members]),
                }
            )
        )
    return pl.concat(frames), invalid, unmapped


def build_tables(
    rec: Recovered,
    stocks: Stocks,
    ratio: adjustment.Ratio,
    preclose: np.ndarray,
    index_members: pl.DataFrame,
    dropped: np.ndarray,
    source: str,
) -> dict[str, pl.DataFrame]:
    """dropped marks Recovered rows left out of bars; stocks left without bars are left out entirely."""
    code = pl.Series("code", rec.codes, dtype=pl.String)
    s = stocks.rows
    bars = pl.DataFrame(
        {
            "code": code.gather(stocks.code_idx),
            "date": stocks.dates,
            **{f: _li(rec.price[f][s], decimals=2) for f in PRICES},
            "volume": _shares(rec.lots[s]),
            "amount": rec.amount_k[s] * 1000,
            "preclose": _li(preclose, decimals=2),
        }
    ).with_columns(
        preclose_derived=pl.lit(True),
        tradestatus=pl.lit(None, dtype=pl.Int8),
        is_st=pl.lit(None, dtype=pl.Boolean),
        source=pl.lit(source),
    ).filter(pl.Series(~dropped[s]))

    i = np.flatnonzero(rec.kind == "index")
    index_bars = pl.DataFrame(
        {
            "code": code.gather(rec.code_idx[i]),
            "date": rec.dates[i],
            **{f: _li(rec.price[f][i], decimals=3) for f in PRICES},
            "volume": _shares(rec.lots[i]),
            "amount": rec.amount_k[i] * 1000,
        }
    ).with_columns(source=pl.lit(source))

    adj_factor = pl.DataFrame(
        {
            "code": code.gather(stocks.code_idx[ratio.event_rows]),
            "ex_date": stocks.dates[ratio.event_rows],
            "factor": ratio.event_ratio,
        }
    )

    listed = [(c, span) for c, span, kind in zip(rec.codes, rec.spans, rec.kinds) if kind == "stock"]
    securities = pl.DataFrame(
        {
            "code": [c for c, _ in listed],
            "exchange": [codemod.split(c)[1] for c, _ in listed],
            "board": [codemod.board(c) for c, _ in listed],
            "first_date": _dates([span.start for _, span in listed]),
            "last_date": _dates([span.end for _, span in listed]),
        }
    ).with_columns(name=pl.lit(None, dtype=pl.String), source=pl.lit(source))

    has_bars = pl.col("code").is_in(bars["code"].unique().implode())
    securities, adj_factor = securities.filter(has_bars), adj_factor.filter(has_bars)
    calendar = pl.DataFrame({"date": rec.calendar[rec.calendar <= rec.dates.max()]})

    tables = {
        "securities": securities,
        "calendar": calendar,
        "bars": bars,
        "adj_factor": adj_factor,
        "index_bars": index_bars,
        "index_members": index_members,
    }
    return {name: df.select(list(TABLES[name].schema)) for name, df in tables.items()}


def convert(root: Path, tag: str) -> tuple[dict[str, pl.DataFrame], dict]:
    """Parse, check and convert an extracted release; raises ConventionError if any check fails."""
    panel = qlib.read_panel(root, FIELDS)
    rec = recover(panel)
    stocks = stocks_of(rec)
    ratio = adjustment.ratio(stocks.code_idx, stocks.close, stocks.adjclose)
    preclose = adjustment.preclose(stocks.code_idx, stocks.close, ratio.row_ratio)
    members, invalid_members, unmapped = read_index_members(root)
    tables = build_tables(rec, stocks, ratio, preclose, members, checks.off_scale(rec), source=f"investment_data@{tag}")
    stats, problems = checks.run_all(panel, rec, stocks, ratio, preclose, tables, invalid_members, unmapped)
    if problems:
        raise ConventionError(problems, stats)
    return tables, stats


def _li(values: np.ndarray, decimals: int) -> pl.Series:
    """Round to `decimals` places and express in 0.001 units as Int32 (NaN becomes null)."""
    li = np.rint(values * 10**decimals) * 10 ** (3 - decimals)
    finite = li[np.isfinite(li)]
    if finite.size and (finite.min() < -(2**31) or finite.max() >= 2**31):
        raise ValueError(f"values do not fit int32: {finite.min()}..{finite.max()}")
    return pl.Series(li, nan_to_null=True).cast(pl.Int32)


def _shares(lots: np.ndarray) -> pl.Series:
    return pl.Series(np.rint(lots * 100)).cast(pl.Int64)


def _dates(values: list) -> pl.Series:
    return pl.Series(np.array(values, dtype="datetime64[D]"))
