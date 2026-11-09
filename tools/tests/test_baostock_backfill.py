import datetime as dt
import json
import os
import re
import signal
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from tests.baostock_fake import FakeResult
from tools import baostock_backfill as bf
from wequant.data import files
from wequant.data.raw import META_COLUMNS, BaoStockRaw
from wequant.data.sources.baostock import BaoStock, GuardedSocket

RECORDED = json.loads((Path(__file__).parents[2] / "tests" / "data" / "baostock.json").read_text("utf-8"))
TODAY = dt.date(2026, 11, 7)  # a Saturday
DAYS = [d for d in (TODAY - dt.timedelta(days=n) for n in range(1, 15)) if d.weekday() < 5]  # 10 trading days


class Server:
    """A fake BaoStock server answering from recorded rows, with dates and codes taken from each request."""

    def __init__(
        self, fail_days=None, bad_days=(), empty_days=(), limit_day=None, kick_day=None, on_snapshot=None, outage=None
    ):
        self.fail_days = dict(fail_days or {})  # day -> failures before it succeeds (999: always)
        self.bad_days, self.limit_day, self.kick_day, self.on_snapshot = set(bad_days), limit_day, kick_day, on_snapshot
        self.empty_days = set(empty_days)  # days answered with the fields but no rows
        self.outage = outage  # (day, failed logins): the server goes down at that day's snapshot until then
        self.down = False
        self.calls = []

    def login(self):
        self.calls.append(("login", None))
        if self.down:
            day, logins = self.outage
            self.outage = (day, logins - 1)
            if logins > 0:
                return SimpleNamespace(error_code="10002007", error_msg="网络接收错误")
            self.down = False
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self):
        self.calls.append(("logout", None))

    def query_stock_basic(self, **params):
        self.calls.append(("stock_basic", None))
        return FakeResult(RECORDED["stock_basic"]["fields"], RECORDED["stock_basic"]["rows"])

    def query_trade_dates(self, start_date, end_date):
        self.calls.append(("trade_dates", None))
        days = [TODAY - dt.timedelta(days=n) for n in range(20)]
        return FakeResult(
            ["calendar_date", "is_trading_day"], [[d.isoformat(), "1" if d in DAYS else "0"] for d in sorted(days)]
        )

    def query_stock_industry(self, **params):
        self.calls.append(("stock_industry", None))
        return FakeResult(
            ["updateDate", "code", "code_name", "industry", "industryClassification"],
            [["2026-11-02", "sh.600000", "浦发银行", "J66货币金融服务", "证监会行业分类"]],
        )

    def query_daily_history_k_AStock(self, date):
        day = dt.date.fromisoformat(date)
        self.calls.append(("snapshot", day))
        if self.on_snapshot:
            self.on_snapshot(self, day)
        if self.outage is not None and day == self.outage[0]:
            self.down, self.outage = True, (None, self.outage[1])  # one outage
        if self.down:
            return FakeResult([], [], "10002007", "网络接收错误")
        if day == self.limit_day:
            return FakeResult([], [], "10001005", "账号登陆数达到上限")
        if day == self.kick_day:
            self.kick_day = None
            return FakeResult([], [], "10001001", "用户未登录")
        if self.fail_days.get(day, 0) > 0:
            self.fail_days[day] -= 1
            return FakeResult([], [], "10002007", "网络接收错误")
        fields = RECORDED["daily_snapshot"]["fields"]
        if day in self.empty_days:
            return FakeResult(fields, [])
        stamp = "2000-01-01" if day in self.bad_days else date
        return FakeResult(fields, [[stamp, *row[1:]] for row in RECORDED["daily_snapshot"]["rows"]])

    def query_adjust_factor(self, code, start_date, end_date):
        self.calls.append(("adjust_factor", code))
        fields = RECORDED["adjust_factor"]["fields"]
        return FakeResult(fields, [[code, *row[1:]] for row in RECORDED["adjust_factor"]["rows"][:3]])

    def snapshots(self):
        return [item for name, item in self.calls if name == "snapshot"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "shanghai_today", lambda: TODAY)
    monkeypatch.setattr(bf, "_baostock_version", lambda: "0.9.4")  # the optional extra may not be installed
    return tmp_path


def backfill(home, server):
    sleeps = []
    job = bf.Backfill(home, client=server, sleep=sleeps.append)
    job.sleeps = sleeps
    return job


def test_a_full_run_writes_every_file_newest_first(home):
    server = Server()
    job = backfill(home, server)
    assert job.run([0, 1, 2]) == 0
    raw = BaoStockRaw.at(home)
    assert raw.daily_days() == set(DAYS)
    assert server.snapshots() == sorted(DAYS, reverse=True)
    assert len(raw.adjust_factor_codes()) == len(bf.stock_codes(raw)) == 11
    snapshot = pl.read_parquet(raw.daily(DAYS[0]))
    assert all(dtype == pl.String for dtype in snapshot.schema.values())
    assert set(META_COLUMNS) <= set(snapshot.columns)
    assert snapshot["_request"][0] == f"query_daily_history_k_AStock(date={DAYS[0]})"
    assert snapshot["date"].unique().to_list() == [DAYS[0].isoformat()]
    progress = json.loads((raw.root / "progress.json").read_text())
    assert progress["requests"] == len(server.calls) and progress["failures"] == {}
    assert server.calls[-1] == ("logout", None)


def test_an_interrupted_run_resumes_without_refetching(home):
    first = Server()
    assert backfill(home, first).run([0, 1, 2], max_requests=7) == 0  # login + 3 reference files + 3 days
    raw = BaoStockRaw.at(home)
    assert raw.daily_days() == set(sorted(DAYS, reverse=True)[:3])
    second = Server()
    assert backfill(home, second).run([1, 2]) == 0
    assert set(first.snapshots()).isdisjoint(second.snapshots())
    assert raw.daily_days() == set(DAYS)


def test_failures_are_listed_and_retried_once_at_the_end(home):
    flaky, broken = DAYS[2], DAYS[5]
    server = Server(fail_days={flaky: 3, broken: 999})
    job = backfill(home, server)
    assert job.run([0, 1]) == 0
    raw = BaoStockRaw.at(home)
    assert flaky in raw.daily_days() and broken not in raw.daily_days()
    assert server.snapshots().count(flaky) == 4  # three attempts, then one more in the retry pass
    progress = json.loads((raw.root / "progress.json").read_text())
    assert list(progress["failures"]) == [f"1:{broken}"]
    assert job.sleeps[:2] == [2.0, 4.0]


def test_a_snapshot_failing_its_checks_is_not_written(home):
    bad = DAYS[1]
    job = backfill(home, Server(bad_days={bad}))
    job.run([0, 1])
    raw = BaoStockRaw.at(home)
    assert bad not in raw.daily_days()
    failure = json.loads((raw.root / "progress.json").read_text())["failures"][f"1:{bad}"]
    assert "rows dated other than" in failure["error"]


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda f: f.head(0), "no rows"),
        (lambda f: f.with_columns(date=pl.lit("2026-11-05")), "rows dated other than"),
        (lambda f: pl.concat([f, f.head(1)]), "duplicate codes"),
        (lambda f: f.with_columns(code=pl.lit("bj.920047")).head(1), "codes not on sh/sz"),
        (lambda f: f.with_columns(tradestatus=pl.lit("0")), "no stock traded"),
        (lambda f: f.drop("tradestatus"), "missing fields"),
    ],
)
def test_snapshot_checks(change, problem):
    case = RECORDED["daily_snapshot"]
    frame = pl.DataFrame(case["rows"], schema={c: pl.String for c in case["fields"]}, orient="row")
    day = dt.date.fromisoformat(case["params"]["date"])
    assert bf.snapshot_problem(frame, day) is None
    assert problem in bf.snapshot_problem(change(frame), day)


def test_an_interrupted_write_leaves_no_file(tmp_path, monkeypatch):
    target = tmp_path / "daily" / "2026" / "2026-11-06.parquet"

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        BaoStockRaw(tmp_path).write(target, pl.DataFrame({"a": ["1"]}))
    assert not target.exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_a_signal_finishes_the_current_file_and_exits_cleanly(home):
    def interrupt(server, day):
        if len(server.snapshots()) == 3:
            os.kill(os.getpid(), signal.SIGINT)

    server = Server(on_snapshot=interrupt)
    job = backfill(home, server)
    assert job.run([0, 1, 2]) == 0
    raw = BaoStockRaw.at(home)
    assert raw.daily_days() == set(sorted(DAYS, reverse=True)[:3])  # the third file was finished
    assert server.calls[-1] == ("logout", None)
    assert not any(name == "adjust_factor" for name, _ in server.calls)
    assert job.progress.runs[-1]["outcome"] == "stopped by signal"
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_a_suspected_daily_limit_stops_at_once(home):
    limited = DAYS[1]
    server = Server(limit_day=limited)
    assert backfill(home, server).run([0, 1, 2]) == bf.DAILY_LIMIT_EXIT
    assert server.snapshots() == [DAYS[0], limited]  # no retry, nothing after it
    assert server.calls[-1] == ("logout", None)


def test_a_kicked_session_logs_in_again(home):
    kicked = DAYS[0]
    server = Server(kick_day=kicked)
    job = backfill(home, server)
    job.run([0, 1])
    assert kicked in BaoStockRaw.at(home).daily_days()
    assert [name for name, _ in server.calls].count("login") == 2


def test_an_outage_pauses_the_run_until_a_login_works(home):
    server = Server(outage=(DAYS[1], 3 * bf.ATTEMPTS + 1))  # three items fail, then the first login after a pause
    job = backfill(home, server)
    assert job.run([0, 1]) == 0
    assert BaoStockRaw.at(home).daily_days() == set(DAYS)  # the three failed days came back in the retry pass
    assert job.progress.failures == {}
    assert sum(job.sleeps) == 3 * (2.0 + 4.0) + bf.DOWN_WAITS[0] + bf.DOWN_WAITS[1]
    assert server.snapshots().count(DAYS[4]) == 1  # nothing was requested in vain after the third failure


def test_a_run_started_during_an_outage_waits_for_the_server(home):
    server = Server(outage=(None, 2))
    server.down = True
    job = backfill(home, server)
    assert job.run([0, 1]) == 0
    assert sum(job.sleeps) == bf.DOWN_WAITS[0] + bf.DOWN_WAITS[1]
    assert BaoStockRaw.at(home).daily_days() == set(DAYS)


def test_a_long_outage_stops_the_run(home):
    job = backfill(home, Server(outage=(DAYS[1], 10**6)))
    assert job.run([0, 1]) == bf.SERVER_DOWN_EXIT
    assert job.progress.runs[-1]["outcome"].startswith("BaoStock unreachable")
    assert bf.DOWN_GIVE_UP <= sum(job.sleeps) - 3 * (2.0 + 4.0) < bf.DOWN_GIVE_UP + bf.DOWN_WAITS[-1]
    assert DAYS[4] not in BaoStockRaw.at(home).daily_days()


def test_a_signal_ends_an_outage_wait(home):
    job = backfill(home, Server(outage=(DAYS[1], 10**6)))

    def sleep(seconds):
        if seconds == bf.PAUSE_STEP:
            os.kill(os.getpid(), signal.SIGTERM)

    job.sleep = sleep
    assert job.run([0, 1]) == 0
    assert job.progress.runs[-1]["outcome"] == "stopped by signal"


def files_under(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()
    }


def test_refetch_writes_the_snapshot_format_under_refetch_only(home):
    backfill(home, Server()).run([0, 1])
    raw = BaoStockRaw.at(home)
    before = files_under(raw.root)
    server = Server()
    assert backfill(home, server).refetch([DAYS[2], DAYS[0], DAYS[2]]) == 0
    after = files_under(raw.root)
    changed = {p for p in after if after[p] != before.get(p)} - {".lock"}
    assert changed == {f"refetch/{DAYS[2]}.parquet", f"refetch/{DAYS[0]}.parquet"}
    assert server.calls == [("login", None), ("snapshot", DAYS[2]), ("snapshot", DAYS[0]), ("logout", None)]
    again = pl.read_parquet(bf.refetch_path(raw, DAYS[2]))
    assert again.columns == pl.read_parquet(raw.daily(DAYS[2])).columns
    assert again.drop("_fetched_at").equals(pl.read_parquet(raw.daily(DAYS[2])).drop("_fetched_at"))
    assert again["_request"][0] == f"query_daily_history_k_AStock(date={DAYS[2]})"


def test_refetch_keeps_a_day_refetched_before(home):
    backfill(home, Server()).refetch([DAYS[0]])
    path = bf.refetch_path(BaoStockRaw.at(home), DAYS[0])
    written = path.stat().st_mtime_ns
    server = Server()
    assert backfill(home, server).refetch([DAYS[0], DAYS[1]]) == 0
    assert server.snapshots() == [DAYS[1]] and path.stat().st_mtime_ns == written
    assert f"refetched before, kept: {DAYS[0]}" in log_text(home)


def test_refetch_reports_a_day_without_rows_and_does_not_write_it(home):
    server = Server(empty_days={DAYS[1]}, fail_days={DAYS[3]: 999})
    job = backfill(home, server)
    assert job.refetch([DAYS[1], DAYS[0], DAYS[3]]) == 0
    raw = BaoStockRaw.at(home)
    assert not bf.refetch_path(raw, DAYS[1]).exists() and bf.refetch_path(raw, DAYS[0]).exists()
    assert server.snapshots() == [DAYS[1], DAYS[0], *[DAYS[3]] * 6]  # an empty answer is not retried; a failure is
    assert job.sleeps == [2.0, 4.0, 2.0, 4.0]
    text = log_text(home)
    assert f"no rows, not written: {DAYS[1]}; written: {DAYS[0]}; failed: {DAYS[3]}" in text
    assert not (raw.root / "progress.json").exists() and raw.daily_days() == set()


def test_refetch_logs_in_again_and_waits_out_an_outage(home):
    server = Server(kick_day=DAYS[0], outage=(DAYS[1], 3 * bf.ATTEMPTS + 1))  # as in the run's outage test
    job = backfill(home, server)
    assert job.refetch(DAYS[:5]) == 0
    raw = BaoStockRaw.at(home)
    assert all(bf.refetch_path(raw, day).exists() for day in DAYS[:5])  # the three failed days came back at the end
    assert job.sleeps[0] == 2.0  # the kicked session's backoff before it logs in again
    assert sum(job.sleeps[1:]) == 3 * (2.0 + 4.0) + bf.DOWN_WAITS[0] + bf.DOWN_WAITS[1]
    assert server.snapshots().count(DAYS[4]) == 1 and server.calls[-1] == ("logout", None)
    assert f"refetch finished (written: {', '.join(map(str, DAYS[:5]))})" in log_text(home)


def test_a_signal_during_a_refetch_finishes_the_current_day_and_skips_the_rest(home):
    def interrupt(server, day):
        if day == DAYS[1]:
            os.kill(os.getpid(), signal.SIGTERM)

    server = Server(fail_days={DAYS[0]: 999}, on_snapshot=interrupt)
    assert backfill(home, server).refetch(DAYS[:3]) == 0
    assert server.snapshots() == [DAYS[0]] * 3 + [DAYS[1]]  # the failed day is not retried after the signal
    assert server.calls[-1] == ("logout", None)
    outcomes = f"failed: {DAYS[0]}; written: {DAYS[1]}; skipped after a signal: {DAYS[2]}"
    assert f"refetch stopped by signal ({outcomes})" in log_text(home)


def test_refetch_refuses_to_start_while_a_run_holds_the_lock(home):
    refused = []

    def refetch_meanwhile(server, day):
        if not refused:
            other = Server()
            with pytest.raises(bf.Busy, match="held by run"):
                backfill(home, other).refetch([day])
            refused.append(other.calls)

    assert backfill(home, Server(on_snapshot=refetch_meanwhile)).run([0, 1]) == 0
    assert refused == [[]]  # no login, no request
    assert not (BaoStockRaw.at(home).root / "refetch").exists()


def test_a_run_refuses_to_start_while_a_refetch_holds_the_lock(home, monkeypatch):
    server = Server()
    monkeypatch.setenv("WEQUANT_HOME", str(home))
    monkeypatch.setattr(bf, "BaoStock", lambda client, retries: BaoStock(client=server, retries=retries))  # no network
    with bf.held(BaoStockRaw.at(home).root / ".lock", "refetch"):
        with pytest.raises(bf.Busy, match="held by refetch"):
            backfill(home, server).run([0, 1])
        assert bf.main(["run"]) == bf.BUSY_EXIT
    assert server.calls == [] and not (BaoStockRaw.at(home).root / "progress.json").exists()
    assert bf.main(["refetch", DAYS[0].isoformat()]) == 0  # the lock is free again


def log_text(home: Path) -> str:
    return (home / "logs" / "baostock_backfill.log").read_text("utf-8")


def file_splits(home: Path) -> list[tuple[str, float, float]]:
    """(label, server s, local s) of every per-file log line, in order."""
    pattern = r"INFO (.+?): (?:\d+ rows|failed), server ([\d.]+) s, local ([\d.]+) s"
    return [(m[1], float(m[2]), float(m[3])) for m in re.finditer(pattern, log_text(home))]


class SlowSocket:
    """A connection whose every reply arrives after `delay` seconds."""

    def __init__(self, delay):
        self.delay = delay

    def settimeout(self, seconds):
        pass

    def recv(self, size):
        time.sleep(self.delay)
        return b"reply"


def test_the_log_splits_each_file_into_server_wait_and_local_time(home, monkeypatch):
    monkeypatch.setattr(bf, "LOG_EVERY", {1: 5, 2: 100})

    def slow_reply_then_decoding(server, day):
        if day == DAYS[0]:
            GuardedSocket(SlowSocket(0.3)).recv(8192)
            time.sleep(0.05)

    backfill(home, Server(on_snapshot=slow_reply_then_decoding)).run([0, 1])
    splits = file_splits(home)
    assert [label for label, _, _ in splits][:4] == [
        "phase 0 stock_basic",
        "phase 0 trade_dates",
        "phase 0 stock_industry",
        f"phase 1 {DAYS[0]}",
    ]
    _, server, local = splits[3]
    assert server >= 0.3 and 0.05 <= local < server
    progress = re.findall(
        r"(\d+) files since the last line: server ([\d.]+) s, local ([\d.]+) s on average", log_text(home)
    )
    assert [int(n) for n, _, _ in progress] == [8, 5]  # three reference files and five days, then five days
    for (_, server_mean, local_mean), lines in zip(progress, (splits[:8], splits[8:13]), strict=True):
        assert float(server_mean) == pytest.approx(statistics.fmean(s for _, s, _ in lines), abs=0.011)
        assert float(local_mean) == pytest.approx(statistics.fmean(lo for _, _, lo in lines), abs=0.011)


def test_backoffs_count_as_neither_server_nor_local_time(home):
    job = backfill(home, Server(fail_days={DAYS[0]: 1}))
    job.sleep = lambda seconds: time.sleep(0.3)
    job.run([0, 1])
    label, server, local = file_splits(home)[3]
    assert label == f"phase 1 {DAYS[0]}" and server == 0 and local < 0.3


def test_an_adjust_factor_answer_without_rows_is_still_written(home):
    server = Server()
    server.query_adjust_factor = lambda **params: FakeResult(RECORDED["adjust_factor"]["fields"], [])
    backfill(home, server).run([0, 2])
    raw = BaoStockRaw.at(home)
    assert len(raw.adjust_factor_codes()) == 11  # a stock without factors is done, not fetched again next run
    assert pl.read_parquet(raw.adjust_factor(bf.stock_codes(raw)[0])).height == 0


def test_status_reads_local_files_only(home):
    backfill(home, Server(fail_days={DAYS[4]: 999})).run([0, 1], max_requests=20)
    text = bf.status(home)
    assert "phase 0: 3 of 3 reference files" in text
    assert "phase 1:" in text and "of 10 days done" in text
    assert "phase 2: 0 of 11 stocks done, 11 remaining" in text
    assert "requests so far" in text


def test_the_report_runs_on_local_files(home):
    from tools.baostock_raw_report import write_report
    from wequant.data.schema import TABLES
    from wequant.data.store import Store

    backfill(home, Server()).run([0, 1, 2])
    first = sorted(DAYS)[0]
    bars = pl.DataFrame(
        {
            "code": "600000.SH",
            "date": sorted(DAYS),
            "open": 9910,
            "high": 10070,
            "low": 9840,
            "close": 10030,
            "volume": 64270729,
            "amount": 640470012.07,
            "preclose": [None, *[9870] * (len(DAYS) - 1)],
        },
        schema_overrides={**{c: pl.Int32 for c in ("open", "high", "low", "close", "preclose")}, "volume": pl.Int64},
    ).with_columns(
        preclose_derived=pl.lit(True),
        tradestatus=pl.lit(None, dtype=pl.Int8),
        is_st=pl.lit(None, dtype=pl.Boolean),
        source=pl.lit("seed"),
    )
    securities = pl.DataFrame(
        {"code": ["600000.SH"], "exchange": ["SH"], "board": ["main"], "first_date": [first], "last_date": [max(DAYS)]}
    ).with_columns(name=pl.lit(None, dtype=pl.String), source=pl.lit("seed"))
    tables = {
        "calendar": pl.DataFrame({"date": sorted(DAYS)}),
        "securities": securities,
        "bars": bars,
        "adj_factor": pl.DataFrame({"code": ["600000.SH"], "ex_date": [first], "factor": [1.0]}),
    }
    Store(home).commit({}, replace={n: t.select(list(TABLES[n].schema)) for n, t in tables.items()})
    text = write_report(home).read_text()
    assert "Open/high/low/close identical:** 3 of 3 rows (100.0000%)" in text  # three sampled days
    assert "4 codes appear in the snapshots but not in the seed's securities" in text
    assert "## 6. Adjustment factors" in text
