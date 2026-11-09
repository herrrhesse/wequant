"""Fetch BaoStock's raw data into $WEQUANT_HOME/raw/baostock/; stop any time, resume any time.

    python -m tools.baostock_backfill run [--phase 0|1|2|all] [--max-requests N]
    python -m tools.baostock_backfill status
    python -m tools.baostock_backfill report

Phases:
    0  stock_basic, trade dates since 2000 and industries; refetched on every run
    1  one-day snapshots of every SH/SZ stock, from the latest trading day back to 2000-01-04
    2  adjustment factors of every SH/SZ stock in stock_basic, delisted ones included
A file that exists is complete and is skipped. Items that fail three times go on a failure list, which is
retried once at the end of the run. When several items fail in a row BaoStock is probably down: the run then
waits, logging in now and then, until it answers again, and stops after 12 hours of waiting.
The program writes raw/baostock/, logs/baostock_backfill.log and reports/ only; it never writes the store.

While it runs, nothing else may use BaoStock, tests marked network included: BaoStock keeps one session per
user, so any other login ends this one.

Running it in the background (Linux), from the wequant repo with WEQUANT_HOME set:

    tmux new -s backfill
    systemd-inhibit --what=sleep --why="baostock backfill" uv run --extra baostock python -m tools.baostock_backfill run
    # detach with Ctrl-b d; reattach with: tmux attach -t backfill

  or without tmux:

    nohup systemd-inhibit --what=sleep --why="baostock backfill" \\
        uv run --extra baostock python -m tools.baostock_backfill run > /dev/null 2>&1 &

  systemd-inhibit --what=sleep needs an active desktop session; from a non-interactive shell use --what=idle.

  progress: python -m tools.baostock_backfill status, or tail -f $WEQUANT_HOME/logs/baostock_backfill.log
  stop:     Ctrl-C in tmux, or kill -TERM <pid>; it finishes the current file, logs out and exits 0
  resume:   run the same command again
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import logging
import signal
import sys
import time
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from wequant.data.files import atomic_write
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.data.sources.baostock import FIRST_DAY, NOT_LOGGED_IN, BaoStock, BaoStockError, from_baostock
from wequant.data.store import wequant_home

ATTEMPTS = 3
BACKOFF = 2.0  # seconds before the second attempt; doubled for the third
OLDEST_DAY = dt.date(2000, 1, 4)
LIMIT_CODES = {"10001005", "10001011", "10004016"}  # login count limit, blacklisted, too many records
LOG_EVERY = {1: 20, 2: 100}
DOWN_AFTER = 3  # items failing in a row: the server is probably down, so wait rather than fail every item
DOWN_WAITS = (300, 600, 1200, 2400, 3600)  # seconds between logins while it is down; the last one repeats
DOWN_GIVE_UP = 12 * 3600  # seconds of waiting before the run stops
PAUSE_STEP = 5  # seconds; waits are cut into steps so a signal ends them
DAILY_LIMIT_EXIT = 3
SERVER_DOWN_EXIT = 4


class DailyLimit(RuntimeError):
    """BaoStock answered with an error that looks like a daily request limit."""


class RequestCap(RuntimeError):
    """--max-requests was reached."""


class ServerDown(RuntimeError):
    """BaoStock did not answer for DOWN_GIVE_UP seconds."""


def shanghai_today() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()


def snapshot_problem(frame: pl.DataFrame, day: dt.date) -> str | None:
    """Why a one-day snapshot must not be written, or None if it passes."""
    missing = {"date", "code", "tradestatus"} - set(frame.columns)
    if missing:
        return f"missing fields {sorted(missing)}"
    if frame.height == 0:
        return "no rows"
    if (frame["date"] != day.isoformat()).any():
        return f"rows dated other than {day}: {sorted(set(frame['date']) - {day.isoformat()})[:3]}"
    if frame["code"].is_duplicated().any():
        return f"duplicate codes: {frame.filter(frame['code'].is_duplicated())['code'].unique().to_list()[:3]}"
    bad = frame.filter(~frame["code"].str.starts_with("sh.") & ~frame["code"].str.starts_with("sz."))
    if bad.height:
        return f"codes not on sh/sz: {bad['code'].to_list()[:3]}"
    if not (frame["tradestatus"] == "1").any():
        return "no stock traded"
    return None


def nonempty(frame: pl.DataFrame) -> str | None:
    return "no rows" if frame.height == 0 else None


def trading_days(raw: BaoStockRaw, today: dt.date) -> list[dt.date]:
    """Trading days from OLDEST_DAY up to the day before `today`, newest first."""
    dates = pl.read_parquet(raw.trade_dates).filter(pl.col("is_trading_day") == "1")["calendar_date"]
    days = (dt.date.fromisoformat(d) for d in dates)
    return sorted((d for d in days if OLDEST_DAY <= d < today), reverse=True)


def stock_codes(raw: BaoStockRaw) -> list[str]:
    """Every SH/SZ stock in stock_basic, listed or delisted, as 600000.SH."""
    basic = pl.read_parquet(raw.stock_basic)
    stocks = basic.filter((pl.col("type") == "1") & pl.col("code").str.contains(r"^s[hz]\."))
    return sorted(from_baostock(c) for c in stocks["code"])


class Progress:
    """Cumulative requests, the failure list and recent speed, kept in raw/baostock/progress.json."""

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.requests: int = data.get("requests", 0)
        self.failures: dict[str, dict] = data.get("failures", {})
        self.seconds_per_item: dict[str, float] = data.get("seconds_per_item", {})
        self.runs: list[dict] = data.get("runs", [])

    def save(self) -> None:
        data = {
            "requests": self.requests,
            "failures": self.failures,
            "seconds_per_item": self.seconds_per_item,
            "runs": self.runs,
        }
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    def timed(self, phase: int, seconds: float) -> None:
        key = str(phase)
        previous = self.seconds_per_item.get(key)
        self.seconds_per_item[key] = seconds if previous is None else 0.95 * previous + 0.05 * seconds


class Backfill:
    def __init__(self, home: Path | None = None, client=None, sleep: Callable[[float], None] = time.sleep):
        self.home = Path(home or wequant_home())
        self.raw = BaoStockRaw.at(self.home)
        self.source = BaoStock(client=client, retries=0)
        self.sleep = sleep
        self.progress = Progress(self.raw.root / "progress.json")
        self.log = _logger(self.home)
        self.stopping = False
        self.max_requests: int | None = None
        self.today = shanghai_today()
        self.counted = 0  # requests already added to progress
        self.failed_in_row = 0

    # ------------------------------------------------------------ running

    def run(self, phases: list[int], max_requests: int | None = None) -> int:
        self.max_requests = max_requests
        handlers = {sig: signal.signal(sig, self._stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        run = {"started": _now(), "phases": phases}
        self.progress.runs.append(run)
        self.raw.remove_temp_files()
        code = 0
        try:
            self._first_login()
            for phase in phases:
                if self.stopping:
                    break
                {0: self.phase0, 1: self.phase1, 2: self.phase2}[phase]()
            if not self.stopping:
                self.retry_failures()
            run["outcome"] = "stopped by signal" if self.stopping else "finished"
        except RequestCap:
            run["outcome"] = f"stopped at --max-requests {max_requests}"
        except DailyLimit as error:
            run["outcome"] = f"suspected daily limit: {error}"
            self.log.error("suspected daily request limit (%s); stopped. Resume tomorrow with the same command.", error)
            code = DAILY_LIMIT_EXIT
        except ServerDown as error:
            run["outcome"] = f"BaoStock unreachable: {error}"
            self.log.error("BaoStock stayed unreachable (%s); stopped. Resume later with the same command.", error)
            code = SERVER_DOWN_EXIT
        finally:
            try:
                self.source.logout()
            except Exception as error:  # the session may already be gone
                self.log.warning("logout failed: %s", error)
            run["ended"], run["requests"] = _now(), self.source.requests
            self._count()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        self.log.info("run %s: %s; %d requests", run["started"], run["outcome"], self.source.requests)
        return code

    def phase0(self) -> None:
        for path, method, params in (
            (self.raw.stock_basic, "query_stock_basic", {}),
            (
                self.raw.trade_dates,
                "query_trade_dates",
                {"start_date": "2000-01-01", "end_date": self.today.isoformat()},
            ),
            (self.raw.stock_industry, "query_stock_industry", {}),
        ):
            frame = self.fetch(0, path.stem, lambda m=method, p=params: self.source.raw(m, **p), nonempty)
            if frame is not None:
                self.raw.write(path, raw_frame(frame, _request(method, params), _baostock_version()))
        self.log.info("phase 0: reference data written")

    def phase1(self) -> None:
        if not self.raw.trade_dates.exists():
            self.log.error("phase 1 needs the trade dates from phase 0")
            return
        done = self.raw.daily_days()
        todo = [day for day in trading_days(self.raw, self.today) if day not in done]
        self.log.info("phase 1: %d days to fetch", len(todo))
        for n, day in enumerate(todo, 1):
            if self.stopping:
                return
            self.snapshot(day)
            if n % LOG_EVERY[1] == 0:
                self._report_progress(1, n, len(todo) - n)

    def phase2(self) -> None:
        if not self.raw.stock_basic.exists():
            self.log.error("phase 2 needs stock_basic from phase 0")
            return
        done = self.raw.adjust_factor_codes()
        todo = [code for code in stock_codes(self.raw) if code not in done]
        self.log.info("phase 2: %d stocks to fetch", len(todo))
        for n, code in enumerate(todo, 1):
            if self.stopping:
                return
            self.factors(code)
            if n % LOG_EVERY[2] == 0:
                self._report_progress(2, n, len(todo) - n)

    def retry_failures(self) -> None:
        failures = list(self.progress.failures.values())
        if failures:
            self.log.info("retrying %d failed items", len(failures))
        if any(f["phase"] == 0 for f in failures):
            self.phase0()
        for failure in failures:
            if self.stopping:
                return
            if failure["phase"] == 1:
                self.snapshot(dt.date.fromisoformat(failure["item"]))
            elif failure["phase"] == 2:
                self.factors(failure["item"])

    # ------------------------------------------------------------ items

    def snapshot(self, day: dt.date) -> None:
        started = time.monotonic()
        frame = self.fetch(
            1, day.isoformat(), lambda: self.source.daily_snapshot(day), lambda f: snapshot_problem(f, day)
        )
        if frame is not None:
            request = _request("query_daily_history_k_AStock", {"date": day})
            self.raw.write(self.raw.daily(day), raw_frame(frame, request, _baostock_version()))
        self.progress.timed(1, time.monotonic() - started)
        self._count()

    def factors(self, code: str) -> None:
        started = time.monotonic()
        bs_code = f"{code[-2:].lower()}.{code[:6]}"
        params = {"code": bs_code, "start_date": FIRST_DAY, "end_date": self.today.isoformat()}

        def problem(frame: pl.DataFrame) -> str | None:
            other = set(frame["code"]) - {bs_code} if "code" in frame.columns else {"no code column"}
            return f"rows for other codes: {sorted(other)[:3]}" if other else None

        frame = self.fetch(2, code, lambda: self.source.raw("query_adjust_factor", **params), problem)
        if frame is not None:
            self.raw.write(
                self.raw.adjust_factor(code),
                raw_frame(frame, _request("query_adjust_factor", params), _baostock_version()),
            )
        self.progress.timed(2, time.monotonic() - started)
        self._count()

    def fetch(
        self, phase: int, item: str, query: Callable[[], pl.DataFrame], check: Callable[[pl.DataFrame], str | None]
    ) -> pl.DataFrame | None:
        """The checked response, or None after ATTEMPTS failures (then the item is on the failure list)."""
        error = ""
        for attempt in range(ATTEMPTS):
            if self.max_requests is not None and self.source.requests >= self.max_requests:
                raise RequestCap()
            try:
                frame = query()
                error = check(frame) or ""
                if not error:
                    self.progress.failures.pop(f"{phase}:{item}", None)
                    self.failed_in_row = 0
                    return frame
            except BaoStockError as exc:
                if looks_like_limit(exc):
                    raise DailyLimit(str(exc)) from exc
                error = str(exc)
                if exc.code == NOT_LOGGED_IN or exc.code.startswith("10002"):  # session ended or connection broke
                    self._login_again()
            self.log.warning("phase %d %s attempt %d failed: %s", phase, item, attempt + 1, error)
            if attempt < ATTEMPTS - 1:
                self.sleep(BACKOFF * 2**attempt)
        self.progress.failures[f"{phase}:{item}"] = {"phase": phase, "item": item, "error": error, "at": _now()}
        self.failed_in_row += 1
        if self.failed_in_row >= DOWN_AFTER:
            self._wait_for_server()
        return None

    # ------------------------------------------------------------ bookkeeping

    def _login_again(self) -> None:
        try:
            self.source.login()
        except BaoStockError as error:  # counts as part of the failed attempt; the next one tries again
            self.log.warning("login failed: %s", error)

    def _first_login(self) -> None:
        """Log in, waiting for the server when it does not answer."""
        try:
            self.source.login()
        except BaoStockError as error:
            if looks_like_limit(error):
                raise DailyLimit(str(error)) from error
            self.log.warning("login failed: %s", error)
            self._wait_for_server()

    def _wait_for_server(self) -> None:
        """Wait, logging in now and then, until BaoStock answers; ServerDown after DOWN_GIVE_UP seconds."""
        waited = 0
        for wait in itertools.chain(DOWN_WAITS, itertools.repeat(DOWN_WAITS[-1])):
            if waited >= DOWN_GIVE_UP:
                raise ServerDown(f"no answer for {waited / 3600:.1f} h")
            self.log.warning("BaoStock does not answer; trying again in %d min", wait // 60)
            if not self._pause(wait):
                return
            waited += wait
            try:
                self.source.login()
            except BaoStockError as error:
                if looks_like_limit(error):
                    raise DailyLimit(str(error)) from error
                self.log.warning("login failed: %s", error)
                continue
            self.failed_in_row = 0
            self.log.info("BaoStock answers again after %d min", waited // 60)
            return

    def _pause(self, seconds: float) -> bool:
        """Sleep in PAUSE_STEP steps; False when a signal stopped the run."""
        for _ in range(int(seconds // PAUSE_STEP)):
            if self.stopping:
                return False
            self.sleep(PAUSE_STEP)
        return not self.stopping

    def _stop(self, signum, frame) -> None:
        self.stopping = True
        self.log.info("signal %d: finishing the current file, then stopping", signum)

    def _count(self) -> None:
        self.progress.requests += self.source.requests - self.counted
        self.counted = self.source.requests
        self.progress.save()

    def _report_progress(self, phase: int, done: int, remaining: int) -> None:
        seconds = self.progress.seconds_per_item.get(str(phase), 0.0)
        self.log.info(
            "phase %d: %d done this run, %d remaining, %.1f s each, about %.1f h left; %d requests in total",
            phase,
            done,
            remaining,
            seconds,
            remaining * seconds / 3600,
            self.progress.requests,
        )


def status(home: Path | None = None) -> str:
    """Progress from local files only; sends no request."""
    backfill_raw = BaoStockRaw.at(home)
    progress = Progress(backfill_raw.root / "progress.json")
    lines = []
    meta = [
        p.stem for p in (backfill_raw.stock_basic, backfill_raw.trade_dates, backfill_raw.stock_industry) if p.exists()
    ]
    lines.append(f"phase 0: {len(meta)} of 3 reference files ({', '.join(meta) or 'none'})")
    for phase, total, done in (
        (
            1,
            trading_days(backfill_raw, shanghai_today()) if backfill_raw.trade_dates.exists() else None,
            backfill_raw.daily_days(),
        ),
        (
            2,
            stock_codes(backfill_raw) if backfill_raw.stock_basic.exists() else None,
            backfill_raw.adjust_factor_codes(),
        ),
    ):
        unit = "days" if phase == 1 else "stocks"
        if total is None:
            lines.append(f"phase {phase}: waiting for phase 0")
            continue
        remaining = len(set(total) - set(done))
        seconds = progress.seconds_per_item.get(str(phase))
        eta = (
            f", about {remaining * seconds / 3600:.1f} h left at {seconds:.1f} s each" if seconds and remaining else ""
        )
        lines.append(
            f"phase {phase}: {len(set(total) & set(done)):,} of {len(total):,} {unit} done, {remaining:,} remaining{eta}"
        )
    by_phase = {}
    for failure in progress.failures.values():
        by_phase[failure["phase"]] = by_phase.get(failure["phase"], 0) + 1
    lines.append(
        f"failure list: {len(progress.failures)} items"
        + (f" ({', '.join(f'phase {k}: {v}' for k, v in sorted(by_phase.items()))})" if by_phase else "")
    )
    lines.append(f"requests so far: {progress.requests:,} over {len(progress.runs)} runs")
    if progress.runs:
        last = progress.runs[-1]
        lines.append(f"last run: started {last['started']}, {last.get('outcome', 'running or killed')}")
    return "\n".join(lines)


def looks_like_limit(error: BaoStockError) -> bool:
    """BaoStock's answer when a login or request limit is hit."""
    return error.code in LIMIT_CODES or any(word in str(error) for word in ("上限", "频繁", "limit"))


def _request(method: str, params: dict) -> str:
    return method + "(" + ", ".join(f"{k}={v}" for k, v in params.items()) + ")"


def _baostock_version() -> str:
    return version("baostock")


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _logger(home: Path) -> logging.Logger:
    """Log to $WEQUANT_HOME/logs/baostock_backfill.log and stderr."""
    log = logging.getLogger("baostock_backfill")
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    path = home / "logs" / "baostock_backfill.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stderr)):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch BaoStock raw data into $WEQUANT_HOME/raw/baostock/.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="fetch; resumes where the last run stopped")
    run.add_argument("--phase", choices=["0", "1", "2", "all"], default="all")
    run.add_argument("--max-requests", type=int, default=None, help="stop after this many requests (default: no limit)")
    commands.add_parser("status", help="show progress (no requests)")
    commands.add_parser("report", help="write the report from local files (no requests)")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(status())
        return 0
    if args.command == "report":
        from tools.baostock_raw_report import write_report

        print(write_report())
        return 0
    phases = [0, 1, 2] if args.phase == "all" else [int(args.phase)]
    return Backfill().run(phases, args.max_requests)


if __name__ == "__main__":
    sys.exit(main())
