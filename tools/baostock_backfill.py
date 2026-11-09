"""Fetch BaoStock's raw data into $WEQUANT_HOME/raw/baostock/; stop any time, resume any time.

    python -m tools.baostock_backfill run [--phase 0|1|2|all] [--max-requests N]
    python -m tools.baostock_backfill refetch DATE [DATE ...]
    python -m tools.baostock_backfill compare [--rows RESOLUTIONS.toml] [--out FILE]
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

refetch fetches the given days' snapshots again into raw/baostock/refetch/<YYYY-MM-DD>.parquet, in the same
format and with the same error handling. It never touches daily/ or progress.json, skips a day refetched before
and reports a day BaoStock answers without rows. compare checks those files against daily/ (local files only).
run and refetch both hold raw/baostock/.lock, so neither starts while the other is running.

The log has one line per file: its rows, the seconds spent waiting for BaoStock's replies (server) and the rest
of its time (local: decoding, checks, writing); backoffs and connecting for a new login count as neither, and
outage waits fall between files. Progress lines show the mean of each over the phase's files since the previous
progress line.

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
import contextlib
import datetime as dt
import fcntl
import itertools
import json
import logging
import os
import signal
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from wequant.data.files import atomic_write
from wequant.data.raw import BaoStockRaw, raw_frame
from wequant.data.sources.baostock import (
    FIRST_DAY,
    NOT_LOGGED_IN,
    BaoStock,
    BaoStockError,
    GuardedSocket,
    from_baostock,
)
from wequant.data.store import wequant_home

Check = Callable[[pl.DataFrame], str | None]  # why a response must not be written, or None

SNAPSHOT = "query_daily_history_k_AStock"
FAILED = "failed"
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
BUSY_EXIT = 5


class Busy(RuntimeError):
    """Another run or refetch holds raw/baostock/.lock."""


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


def refetch_path(raw: BaoStockRaw, day: dt.date) -> Path:
    return raw.root / "refetch" / f"{day.isoformat()}.parquet"


def refetched_days(raw: BaoStockRaw) -> list[dt.date]:
    return sorted(dt.date.fromisoformat(p.stem) for p in (raw.root / "refetch").glob("*.parquet"))


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
        self.lock = self.raw.root / ".lock"
        self.source = BaoStock(client=client, retries=0)
        self.sleep = sleep
        self.progress = Progress(self.raw.root / "progress.json")
        self.log = _logger(self.home)
        self.stopping = False
        self.max_requests: int | None = None
        self.today = shanghai_today()
        self.counted = 0  # requests already added to progress
        self.failed_in_row = 0
        self.excluded = 0.0  # seconds of backoffs, outage waits and connecting, kept out of the timing split
        self.split: list[tuple[float, float]] = []  # (server, local) seconds of each file since the last progress line

    # ------------------------------------------------------------ running

    def run(self, phases: list[int], max_requests: int | None = None) -> int:
        with held(self.lock, "run"):
            self.max_requests = max_requests
            run = {"started": _now(), "phases": phases}
            self.progress.runs.append(run)
            self.raw.remove_temp_files()
            try:
                code, run["outcome"] = self._session(lambda: self._phases(phases))
            finally:
                run["ended"], run["requests"] = _now(), self.source.requests
                self._count()
        self.log.info("run %s: %s; %d requests", run["started"], run["outcome"], self.source.requests)
        return code

    def refetch(self, days: list[dt.date]) -> int:
        """Fetch these days' snapshots again into raw/baostock/refetch/; daily/ and progress.json stay untouched."""
        outcomes = dict.fromkeys(days, "not fetched")

        def fetch(day: dt.date) -> None:
            outcomes[day] = self._refetch(day)
            if outcomes[day] == FAILED:
                self._failed()

        def fetch_all() -> None:
            for day in list(outcomes):
                fetch(day)
            for day in [d for d, outcome in outcomes.items() if outcome == FAILED]:  # once more, as run retries
                if self.stopping:
                    return
                fetch(day)

        with held(self.lock, "refetch"):
            code, outcome = self._session(fetch_all)
        self.log.info("refetch %s (%s); %d requests", outcome, _grouped(outcomes), self.source.requests)
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
            self._item(0, path.stem, path, method, params, nonempty)
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
        params = {"date": day.isoformat()}
        self._item(1, day.isoformat(), self.raw.daily(day), SNAPSHOT, params, lambda f: snapshot_problem(f, day))

    def factors(self, code: str) -> None:
        bs_code = f"{code[-2:].lower()}.{code[:6]}"
        params = {"code": bs_code, "start_date": FIRST_DAY, "end_date": self.today.isoformat()}

        def problem(frame: pl.DataFrame) -> str | None:
            other = set(frame["code"]) - {bs_code} if "code" in frame.columns else {"no code column"}
            return f"rows for other codes: {sorted(other)[:3]}" if other else None

        self._item(2, code, self.raw.adjust_factor(code), "query_adjust_factor", params, problem)

    def _refetch(self, day: dt.date) -> str:
        """Fetch one day again; how it went: written, no rows, failed, refetched before or skipped."""
        path = refetch_path(self.raw, day)
        if self.stopping:
            return "skipped after a signal"
        if path.exists():
            return "refetched before, kept"

        def check(frame: pl.DataFrame) -> str | None:
            return snapshot_problem(frame, day) if frame.height else None  # an empty answer is reported, not retried

        frame, _ = self._file(f"refetch {day}", path, SNAPSHOT, {"date": day.isoformat()}, check, write_empty=False)
        if frame is None:
            return FAILED
        return "written" if frame.height else "no rows, not written"

    def _item(self, phase: int, item: str, path: Path, method: str, params: dict, check: Check) -> None:
        """One backfill file; one that keeps failing goes on the failure list."""
        started = time.monotonic()
        frame, error = self._file(f"phase {phase} {item}", path, method, params, check)
        key = f"{phase}:{item}"
        if frame is None:
            self.progress.failures[key] = {"phase": phase, "item": item, "error": error, "at": _now()}
        else:
            self.progress.failures.pop(key, None)
        self.progress.timed(phase, time.monotonic() - started)
        self._count()
        if frame is None:  # after the save, so a run that gives up waiting for the server keeps it listed
            self._failed()

    def _file(
        self, label: str, path: Path, method: str, params: dict, check: Check, write_empty: bool = True
    ) -> tuple[pl.DataFrame | None, str]:
        """Fetch one query, write the response that passes check to path (one without rows only if write_empty)
        and log the time split."""
        started, waited, excluded = time.monotonic(), GuardedSocket.waited, self.excluded
        frame, error = self.fetch(label, lambda: self.source.raw(method, **params), check)
        if frame is not None and (frame.height or write_empty):
            self.raw.write(path, raw_frame(frame, _request(method, params), _baostock_version()))
        server = GuardedSocket.waited - waited
        local = time.monotonic() - started - server - (self.excluded - excluded)
        self.split.append((server, local))
        rows = "failed" if frame is None else f"{frame.height} rows"
        self.log.info("%s: %s, server %.2f s, local %.2f s", label, rows, server, local)
        return frame, error

    def fetch(self, label: str, query: Callable[[], pl.DataFrame], check: Check) -> tuple[pl.DataFrame | None, str]:
        """(the checked response, "") or, after ATTEMPTS failures, (None, the last error)."""
        error = ""
        for attempt in range(ATTEMPTS):
            if self.max_requests is not None and self.source.requests >= self.max_requests:
                raise RequestCap()
            try:
                frame = query()
                error = check(frame) or ""
                if not error:
                    self.failed_in_row = 0
                    return frame, ""
            except BaoStockError as exc:
                if looks_like_limit(exc):
                    raise DailyLimit(str(exc)) from exc
                error = str(exc)
                if exc.code == NOT_LOGGED_IN or exc.code.startswith("10002"):  # session ended or connection broke
                    self._login_again()
            self.log.warning("%s attempt %d failed: %s", label, attempt + 1, error)
            if attempt < ATTEMPTS - 1:
                self._rest(BACKOFF * 2**attempt)
        return None, error

    # ------------------------------------------------------------ bookkeeping

    def _phases(self, phases: list[int]) -> None:
        for phase in phases:
            if self.stopping:
                return
            self.split.clear()  # each progress line averages its own phase's files
            {0: self.phase0, 1: self.phase1, 2: self.phase2}[phase]()
        if not self.stopping:
            self.retry_failures()

    def _session(self, body: Callable[[], None]) -> tuple[int, str]:
        """Run body logged in, stopping cleanly on SIGINT or SIGTERM; (exit code, outcome)."""
        handlers = {sig: signal.signal(sig, self._stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            self._first_login()
            body()
            return 0, "stopped by signal" if self.stopping else "finished"
        except RequestCap:
            return 0, f"stopped at --max-requests {self.max_requests}"
        except DailyLimit as error:
            self.log.error("suspected daily request limit (%s); stopped. Resume tomorrow with the same command.", error)
            return DAILY_LIMIT_EXIT, f"suspected daily limit: {error}"
        except ServerDown as error:
            self.log.error("BaoStock stayed unreachable (%s); stopped. Resume later with the same command.", error)
            return SERVER_DOWN_EXIT, f"BaoStock unreachable: {error}"
        finally:
            try:
                self.source.logout()
            except Exception as error:  # the session may already be gone
                self.log.warning("logout failed: %s", error)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    def _failed(self) -> None:
        """Count a file that failed every attempt; after DOWN_AFTER in a row, wait for the server."""
        self.failed_in_row += 1
        if self.failed_in_row >= DOWN_AFTER:
            self._wait_for_server()

    def _login(self) -> None:
        """Log in; the time not spent waiting for the reply (connecting) counts as neither server nor local."""
        started, waited = time.monotonic(), GuardedSocket.waited
        try:
            self.source.login()
        finally:
            self.excluded += time.monotonic() - started - (GuardedSocket.waited - waited)

    def _login_again(self) -> None:
        try:
            self._login()
        except BaoStockError as error:  # counts as part of the failed attempt; the next one tries again
            self.log.warning("login failed: %s", error)

    def _first_login(self) -> None:
        """Log in, waiting for the server when it does not answer."""
        try:
            self._login()
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
                self._login()
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
            self._rest(PAUSE_STEP)
        return not self.stopping

    def _rest(self, seconds: float) -> None:
        started = time.monotonic()
        self.sleep(seconds)
        self.excluded += time.monotonic() - started

    def _stop(self, signum, frame) -> None:
        self.stopping = True
        self.log.info("signal %d: finishing the current file, then stopping", signum)

    def _count(self) -> None:
        self.progress.requests += self.source.requests - self.counted
        self.counted = self.source.requests
        self.progress.save()

    def _report_progress(self, phase: int, done: int, remaining: int) -> None:
        seconds = self.progress.seconds_per_item.get(str(phase), 0.0)
        server, local = map(statistics.fmean, zip(*self.split)) if self.split else (0.0, 0.0)
        self.log.info(
            "phase %d: %d done this run, %d remaining, %.1f s each, about %.1f h left; %d requests in total; "
            "%d files since the last line: server %.2f s, local %.2f s on average",
            phase,
            done,
            remaining,
            seconds,
            remaining * seconds / 3600,
            self.progress.requests,
            len(self.split),
            server,
            local,
        )
        self.split.clear()


@contextlib.contextmanager
def held(path: Path, command: str) -> Iterator[None]:
    """Hold an exclusive lock on path while the block runs; Busy names the holder when another process has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            raise Busy(f"{path} is held by {handle.read() or 'another process'}") from None
        handle.truncate(0)
        handle.write(f"{command} (pid {os.getpid()}, since {_now()})")
        handle.flush()
        try:
            yield
        finally:
            handle.truncate(0)


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


def _grouped(outcomes: dict[dt.date, str]) -> str:
    """'written: 2020-06-17, 2020-07-21; failed: ...' in the order the outcomes first occur."""
    days: dict[str, list[str]] = {}
    for day, outcome in outcomes.items():
        days.setdefault(outcome, []).append(day.isoformat())
    return "; ".join(f"{outcome}: {', '.join(found)}" for outcome, found in days.items())


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch BaoStock raw data into $WEQUANT_HOME/raw/baostock/.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="fetch; resumes where the last run stopped")
    run.add_argument("--phase", choices=["0", "1", "2", "all"], default="all")
    run.add_argument("--max-requests", type=int, default=None, help="stop after this many requests (default: no limit)")
    refetch = commands.add_parser("refetch", help="fetch these days again into raw/baostock/refetch/")
    refetch.add_argument("days", nargs="+", type=dt.date.fromisoformat, metavar="DATE")
    check = commands.add_parser("compare", help="compare the refetched days with daily/ (no requests)")
    check.add_argument("--rows", type=Path, help="resolutions file whose listed rows get their own section")
    check.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    commands.add_parser("status", help="show progress (no requests)")
    commands.add_parser("report", help="write the report from local files (no requests)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "status":
        print(status())
        return 0
    if args.command == "report":
        from tools.baostock_raw_report import write_report

        print(write_report())
        return 0
    if args.command == "compare":
        from tools.baostock_compare import compare

        text = compare(rows=args.rows)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    job = Backfill()
    try:
        if args.command == "refetch":
            return job.refetch(args.days)
        return job.run([0, 1, 2] if args.phase == "all" else [int(args.phase)], args.max_requests)
    except Busy as error:
        job.log.error("not started: %s", error)
        return BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
