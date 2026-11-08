"""Markdown report of an import run."""

from __future__ import annotations

from tools.investment_data.adjustment import BLIP_RETURN, MIN_STEP
from tools.investment_data.checks import RESIDUAL_FEN, UNIT_YEAR_MIN, YOUNG_DAYS


def render(run: dict, stats: dict) -> str:
    out = [f"# Seed import: investment_data {run.get('tag', '?')}", "", f"**Status:** {run['status']}", ""]
    if run.get("problems"):
        out += ["Stopped because:", "", *[f"- {p}" for p in run["problems"]], ""]
    out += _summary(run, stats)
    if "verification" in run:
        out += _verification(run)
    if "fields" in stats:
        out += (
            _fields(stats)
            + _rounding(stats)
            + _units(stats)
            + _ohlc(stats)
            + _coverage(stats)
            + _adjustment(stats)
            + _issues(stats)
        )
    out += _idempotency(run) + _resources(run)
    return "\n".join(out) + "\n"


def _table(header: list[str], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(x) for x in row) + " |" for row in rows] + [""]


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _summary(run: dict, s: dict) -> list[str]:
    out = [f"- Data root: `{run['home']}`"]
    if "manifest" in run:
        out.append(f"- Store version: {run['manifest']['version']} (created {run['manifest']['created_at']})")
    if "last_trade_date" in s:
        out.append(f"- Last trading day with data: {s['last_trade_date']}")
        out.append("- Rows: " + ", ".join(f"{k} {v:,}" for k, v in s["table_rows"].items()))
    return out + [
        "",
        "Conversion rules:",
        "",
        f"- **adj_factor:** a new ratio starts when no single ratio fits consecutive days within adjclose's 0.01 元 rounding, and it is recorded only if it moves at least {MIN_STEP:g}.",
        f"- **One-day blips:** a one-day ratio jump whose neighbours agree within {BLIP_RETURN:g} is left out of adj_factor and preclose. They are listed in section 7.",
        "- **Prices:** stock prices are rounded to 0.01 元, index points to 0.001.",
        "- **Dropped:** stock rows whose average price lies outside the day's low–high range (section 4), stocks left with no rows, and index member spans with malformed codes (section 6).",
        "",
    ]


def _verification(run: dict) -> list[str]:
    v, m = run["verification"], run["verification"]["manifest"]
    return [
        "## 1. Archive verification",
        "",
        f"- **Manifest:** release {m['release_tag']}, target trade date {m['target_trade_date']}, future days {m['future_start_date']}..{m['future_end_date']}.",
        f"- **Commits:** dolt `{m['dolt_commit']}`, investment_data `{m['investment_data_commit']}`, qlib `{m['qlib_commit']}`.",
        f"- **Archive:** {v['archive_size_bytes']:,} bytes, `{v['archive_sha256']}`. Matches the manifest.",
        f"- **Members:** {v['archive_files']:,} files; paths are safe and all required members are present.",
        f"- **Calendar:** day.txt runs {v['calendar_first']}..{v['calendar_last']} ({v['calendar_days']:,} days) and ends on the target trade date, as do all instrument lists. day_future.txt adds {v['future_days']} days.",
        f"- **Extracted files:** {run['extraction']['status']} ({run['extraction']['files']:,} files). Each is hashed against its archive member; hashes are in `extracted.sha256`.",
        "",
    ]


def _fields(s: dict) -> list[str]:
    f, fc = s["fields"], s["first_close"]
    return [
        "## 2. Fields and coverage",
        "",
        f"{f['rows']:,} instrument-days inside the instrument spans; {f['rows_with_data']:,} have data.",
        "",
        *_table(
            ["field", "finite rows", "coverage"],
            [(name, f"{n:,}", f"{share:.4%}") for name, (n, share) in f["coverage"].items()],
        ),
        f"- **Instruments with mismatched files:** {len(s['layout']['issues'])}",
        *[f"- **{name.capitalize()}:** {count}" for name, count in f["anomalies"].items()],
        f"- **Days without data:** NaN in every field except the forward-filled factor ({f['factor_filled_rows']:,} rows). They get no row.",
        f"- **First stored close = 1:** {fc['within_1e-6']:.2%} of {fc['instruments']:,} instruments within 1e-6 ({fc['within_1e-3']:.2%} within 1e-3).",
        "",
    ]


def _rounding(s: dict) -> list[str]:
    r = s["rounding"]
    out = ["## 3. Price rounding (stocks)", "", "|raw − round(raw, 2)| in fen:", ""]
    out += _table(
        ["field", "max", "99.99th pct", f"> {RESIDUAL_FEN} fen"],
        [(f, f"{x['max']:.4f}", f"{x['p99.99']:.4f}", x["over"]) for f, x in r["fields"].items()],
    )
    if r["worst"]:
        out += ["Worst closes:", "", *_table(["code", "date", "raw close", "fen"], r["worst"])]
    return out


def _units(s: dict) -> list[str]:
    u = s["units"]
    out = [
        "## 4. Units",
        "",
        f"amount × 1000 / (volume × 100) must lie in [low × 0.99, high × 1.01]; a year below {UNIT_YEAR_MIN} stops the import.",
        "",
    ]
    out += _table(
        ["year", "stock rows", "pass"], [(y, f"{n:,}", f"{share:.5f}") for y, (n, share) in u["by_year"].items()]
    )
    r = u["rule_change"]
    out += [
        "A row is dropped when its average price lies outside [low × (1 − tol), high × (1 + tol)], where tol = 0.01 + 0.5 / amount (千元).",
        f"This drops {r['new_rows']:,} rows; the earlier screen (average price outside 0.5×..2× of the close) dropped {r['old_rows']:,}.",
        "",
    ]
    columns = ["code", "date", "low", "high", "close", "average price"]
    if r["kept"]:
        out += [f"Kept now, dropped by the earlier screen ({len(r['kept'])}):", "", *_table(columns, r["kept"])]
    if r["added"]:
        out += [f"Dropped now, kept by the earlier screen ({len(r['added'])}):", "", *_table(columns, r["added"])]
    out += [f"**Dropped rows with low = high (limit-locked days): {len(u['flat_dropped'])}.**", ""]
    if u["flat_dropped"]:
        out += _table(columns, u["flat_dropped"])
    if u["dropped"]:
        out += [f"Dropped rows by code ({u['dropped_rows']:,}):", ""]
        out += _table(["code", "rows", "rows of code", "first", "last", "median ratio to close"], u["dropped"])
    out.append(f"The stored vwap equals amount × 10 / volume to {u['vwap_max_rel']:.1e} relative.")
    if u["index_points"]:
        lo, hi = u["index_share_price"]
        out.append(
            f"For indices amount / volume is an average share price ({lo:.2f}..{hi:.2f} 元), not index points, so the check does not apply."
        )
    return out + [""]


def _ohlc(s: dict) -> list[str]:
    out = ["## 5. OHLC consistency", ""]
    for name, check in s["ohlc"].items():
        out += [f"**{name}:** " + ", ".join(f"{k}: {v}" for k, v in check["counts"].items()), ""]
        if check["sample"]:
            out += _table(["code", "date", "open", "high", "low", "close", "volume"], check["sample"])
    return out


def _coverage(s: dict) -> list[str]:
    k, c = s["kinds"], s["coverage"]
    days, first, kept, last = c["calendar"]
    out = [
        "## 6. Coverage",
        "",
        f"- **Instruments:** {k['counts']['stock']:,} stocks, {k['counts']['index']} indices ({', '.join(k['indices'])}), {k['counts']['fund']} funds, {k['counts']['unknown']} unclassified.",
        f"- **Identical index series:** {', '.join(f'{a} = {b}' for a, b in s['duplicates']['pairs']) or 'none'}.",
        f"- **BSE stocks:** {k['bse']['old']} old codes (4/8xxxxx) and {k['bse']['new']} new codes (920xxx), not merged.",
        f"- **Calendar:** {days:,} days from {first}; {kept:,} kept, up to {last}.",
        f"- **Stocks whose data ends more than 30 days before {last}:** {c['stale_stocks']}.",
        f"- **Stale indices:** {', '.join(f'{code} (last {d})' for code, d in c['stale_indices']) or 'none'}.",
        f"- **Stocks removed because every row was dropped:** {', '.join(c['removed_stocks']) or 'none'}.",
        "- **index_members:** "
        + ", ".join(f"{code} {n:,}" for code, n in c["members"].items())
        + f"; {c['members_not_listed']} spans name codes not in all.txt.",
        "",
    ]
    if c["dropped_members"]:
        out += [f"Dropped index member spans with malformed codes ({len(c['dropped_members'])}):", ""]
        out += _table(["file", "code", "start", "end"], c["dropped_members"])
    out += _table(["year", "stocks", "rows"], [(y, f"{n:,}", f"{r:,}") for y, n, r in c["by_year"]])
    return out


def _adjustment(s: dict) -> list[str]:
    a = s["adjustment"]
    sizes = zip(["[1e-4, 1e-3)", "[1e-3, 1e-2)", "[1e-2, 0.1)", "≥ 0.1"], a["event_sizes"])
    out = [
        "## 7. adj_factor",
        "",
        f"- **Daily ratio adjclose / raw close:** {a['daily_steps']:,} day-to-day steps; {a['daily_over_1e-5']:,} move more than 1e-5, {a['daily_1e-6_to_1e-4']:,} fall in [1e-6, 1e-4].",
        f"- **Rounding-aware runs:** {a['runs']:,} runs with {a['breaks']:,} breaks between them ({a['breaks_1e-6_to_1e-4']:,} in [1e-6, 1e-4]). {a['merged']:,} breaks below {MIN_STEP:g} were merged and {len(a['blips'])} one-day blips skipped.",
        f"- **adj_factor:** {a['rows']:,} rows, {a['events']:,} of them events after each stock's first day.",
        "- **Event sizes:** " + ", ".join(f"{label}: {n:,}" for label, n in sizes) + ".",
        f"- **Negative events:** {a['negative_events']:,}.",
        f"- **Derived preclose** differs from the previous close on {a['preclose_mismatch']} days without an event.",
        "",
        "Events per stock-year:",
        "",
        *_table(["events", "stock-years"], sorted(a["events_per_code_year"].items())),
    ]
    if a["big_drops"]:
        out += [f"Ratio drops larger than 1% ({len(a['big_drops'])}), for review:", ""]
        out += _table(
            ["code", "date", "step", "before", "after"],
            [(c, d, f"{x:+.4f}", f"{r0:.4f}", f"{r1:.4f}") for c, d, x, r0, r1 in a["big_drops"]],
        )
    if a["blips"]:
        out += [f"Skipped one-day blips ({len(a['blips'])}):", ""]
        out += _table(
            ["code", "date", "close", "adjclose", "ratio before", "blip", "ratio after"],
            [
                (c, d, f"{p:.2f}", f"{x:.2f}", f"{r0:.4f}", f"{r1:.4f}", f"{r2:.4f}")
                for c, d, p, x, r0, r1, r2 in a["blips"]
            ],
        )
    return out


def _issues(s: dict) -> list[str]:
    i = s["issues"]
    out = ["## 8. Data issues", "", "Everything dropped or bypassed is recorded in the data_issues table:", ""]
    out += _table(["kind", "action", "rows"], [(k, a, f"{n:,}") for k, a, n in i["by_kind"]])
    out.append(
        f"**off_scale rows within {YOUNG_DAYS} trading days of the stock's first day: {len(i['young_off_scale'])}.** These may be real first-days trading, when no price limit applies, dropped by mistake."
    )
    out.append("")
    if i["young_off_scale"]:
        out += _table(["code", "date", "scale", "days since first"], i["young_off_scale"])
    return out


def _idempotency(run: dict) -> list[str]:
    out = ["## 9. Idempotency", ""]
    if "commit" in run:
        c = run["commit"]
        out.append(
            f"- **Commit:** version {c.version}, {'new version' if c.changed else 'unchanged'}; {len(c.files_written)} files written, {len(c.files_reused)} reused."
        )
        out.append(f"- **Files written:** {', '.join(c.files_written) or 'none'}.")
    if "repeat" in run:
        r = run["repeat"]
        verdict = "PASS" if r["same_hashes"] and not r["changed"] else "FAIL"
        out.append(
            f"- **Re-import of the same input:** {verdict}. All {r['files']} files hash the same: {r['same_hashes']}; new version: {r['changed']}."
        )
    return out + [""]


def _resources(run: dict) -> list[str]:
    out = [
        "## 10. Time and disk",
        "",
        *_table(["step", "seconds"], [(k, f"{v:.1f}") for k, v in run["timings"].items()]),
    ]
    if "disk" in run:
        d = run["disk"]
        out += [
            f"- **Archive:** {_size(d['archive'])}",
            f"- **Extracted:** {_size(d['extracted'])}",
            f"- **Parquet tables:** {_size(d['tables'])} in the current version, {_size(d['tables_on_disk'])} on disk",
            "",
        ]
    return out
