#!/usr/bin/env bash
# Regenerate fixtures/ from the real wequant CLI. Read-only: never --commit, never BaoStock.
# D20: fixtures/ is local only and never committed. Besides the JSON it gets commands.json (each fixture's command,
# so the page shows only commands that ran) and index.ts (typed loaders the app uses instead of the synthetic data).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/fixtures"
UV="${UV:-/home/sure/micromamba/envs/quant/bin/uv}"
# A frontend-only home: symlinks to the Database tables (read-only) plus its own runs/ and strategies/, so backtest
# archives never land in Database. See README "Local fixtures".
HOME_DIR="${WEQUANT_FIXTURE_HOME:-/home/sure/Projects/quant/wequant_frontend_dev/wq-home}"
CACHE_DIR="${WEQUANT_FIXTURE_CACHE:-/home/sure/Projects/quant/wequant_frontend_dev/cache}"

rm -rf "$OUT"
mkdir -p "$OUT"
RUNS="$(mktemp)"
trap 'rm -f "$RUNS"' EXIT

# fixture NAME ARGS...: run `wequant ARGS... --json` and keep stdout as fixtures/NAME.json.
fixture() {
  local name="$1"
  shift
  mkdir -p "$(dirname "$OUT/$name.json")"
  (cd "$REPO" && "$UV" run --frozen wequant "$@" --home "$HOME_DIR" --cache "$CACHE_DIR" --json) >"$OUT/$name.json"
  python3 -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "args": sys.argv[2:]}))' "$name" "$@" >>"$RUNS"
  echo "fixtures/$name.json"
}

fixture data-status data status
old=$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["versions"]; print(v[-2] if len(v) > 1 else v[-1])' "$OUT/data-status.json")
fixture data-status-old data status --data-version "$old"
fixture data-issues data issues
fixture data-issues-bars-removed-security data issues --table bars --kind removed_security --limit 50
fixture data-issues-bars-off-scale data issues --table bars --kind off_scale
fixture data-issues-adj-factor-adj-blip data issues --table adj_factor --kind adj_blip --limit 50
fixture data-issues-index-members-invalid-code data issues --table index_members --kind invalid_member_code

# Stock page fixtures. Each stock covers one case the page must show (store v3, checked when picked):
#   600000.SH  普通主板股：500 天全部有成交
#   600863.SH  有停牌区间：2025-02-11 至 2025-02-20 停牌 8 天，另有涨停日
#   000725.SZ  有涨停和跌停：涨停 4 天、跌停 3 天，其中 1 天一字（limit_locked）
#   300418.SZ  创业板，涨跌幅 20%：涨停 5 天、跌停 1 天
#   000059.SZ  有数据问题：2025-08-26 的 adj_blip（复权因子跳点）
#   600036.SH  长区间：约 2,500 个交易日，用来测渲染性能
# Short stocks start at 2024-10-17 (about 500 trading days to 2026-11-06); the long one at 2016-07-20.
BAR_FIELDS=(open high low close volume amount adj_open adj_high adj_low adj_close limit_up_price limit_down_price
  limit_up limit_down limit_locked limit_known st st_known factor)
SHORT_START=2024-10-17
LONG_START=2016-07-20
stock() {
  local code="$1" start="$2"
  fixture "stock/$code/bars" data bars --codes "$code" --start "$start" --fields "${BAR_FIELDS[@]}"
  fixture "stock/$code/issues" data issues --codes "$code"
  for n in 5 10 20 60; do
    fixture "stock/$code/ma-adj-$n" expr eval --form "[\"MA\",\"adj_close\",$n]" --codes "$code" --start "$start"
    fixture "stock/$code/ma-raw-$n" expr eval --form "[\"MA\",\"close\",$n]" --codes "$code" --start "$start"
  done
}
for code in 600000.SH 600863.SH 000725.SZ 300418.SZ 000059.SZ; do stock "$code" "$SHORT_START"; done
stock 600036.SH "$LONG_START"

# Strategy examples: the backend's current validate and summary output for a fixed valid strategy, and validate's
# ErrorBody for a deliberately invalid one (a range error, a bad list_days bound and an unknown field in an
# expression, so cross-field rules are not reached: complete = false). The strategies hold no market data.
strategy_fixture() {
  local name="$1" file="$2"
  shift 2
  mkdir -p "$(dirname "$OUT/$name.json")"
  # An invalid strategy exits 2 with the ErrorBody on stdout; that is the output wanted here.
  (cd "$REPO" && "$UV" run --frozen wequant "$@" --strategy - --home "$HOME_DIR" --cache "$CACHE_DIR" --json <"$file") >"$OUT/$name.json" || true
  python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$OUT/$name.json"
  python3 -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "args": sys.argv[2:] + ["--strategy", "-"]}))' "$name" "$@" >>"$RUNS"
  echo "fixtures/$name.json"
}
mkdir -p "$OUT/strategy/example" "$OUT/strategy/invalid"
cat >"$OUT/strategy/example/strategy.json" <<'JSON'
{"pool":{"index":"000906.SH","exclude_st":true,"min_list_days":250},"filters":[{"name":"20日均成交额大于5亿元","expr":["GT",["MA","amount",20],500000000]},{"name":"趋势与波动","conditions":[{"name":"60日波动率在后50%","expr":["LE",["CS_PCT",["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60]],0.5]},{"name":"站上60日均线","expr":["GT",["SUB",["DIV","adj_close",["MA","adj_close",60]],1],0]}]}],"factors":[{"name":"动量","expr":["SUB",["DIV","adj_close",["REF","adj_close",20]],1]},{"name":"低波","expr":["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60],"prefer":"low"}],"top_n":20,"backups":5,"hold_rank":30,"max_weight":0.15}
JSON
cat >"$OUT/strategy/invalid/strategy.json" <<'JSON'
{"pool":{"index":"000906.SH","exclude_st":true,"min_list_days":1},"filters":[{"name":"20日均成交额大于5亿元","expr":["GT",["MA","amout",20],500000000]},{"name":"趋势与波动","conditions":[{"name":"60日波动率在后50%","expr":["LE",["CS_PCT",["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60]],0.5]},{"name":"站上60日均线","expr":["GT",["SUB",["DIV","adj_close",["MA","adj_close",60]],1],0]}]}],"factors":[{"name":"动量","expr":["SUB",["DIV","adj_close",["REF","adj_close",20]],1]},{"name":"动量","expr":["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60],"prefer":"low"}],"top_n":0,"backups":5,"hold_rank":10,"max_weight":0.15}
JSON
strategy_fixture strategy/example/validate "$OUT/strategy/example/strategy.json" strategy validate
strategy_fixture strategy/example/summary "$OUT/strategy/example/strategy.json" strategy summary
strategy_fixture strategy/invalid/validate "$OUT/strategy/invalid/strategy.json" strategy validate

fixture data-delistings data delistings

# Screen and backtest pages: two example strategies (inputs, no market data), screened on the last trading day (the
# first also on a past day) and backtested over about four and five years; the first also with --report.
#   s1  CSI 800, liquidity and low-volatility filters, momentum and low-volatility factors, top 20 + 5 backups
#   s2  the same with an "any" group, hold_rank 30, score weighting and max_weight 5.5% (some weights capped)
mkdir -p "$OUT/runs-input"
cat >"$OUT/runs-input/s1.json" <<'JSON'
{"pool":{"index":"000906.SH","exclude_st":true,"min_list_days":250},"filters":[{"name":"20日均成交额大于3亿元","expr":["GT",["MA","amount",20],300000000]},{"name":"60日波动率在后50%","expr":["LE",["CS_PCT",["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60]],0.5]}],"factors":[{"name":"动量","expr":["SUB",["DIV","adj_close",["REF","adj_close",20]],1]},{"name":"低波","expr":["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60],"prefer":"low"}],"top_n":20,"backups":5}
JSON
cat >"$OUT/runs-input/s2.json" <<'JSON'
{"pool":{"index":"000906.SH","exclude_st":true,"min_list_days":250},"filters":[{"name":"20日均成交额大于3亿元","expr":["GT",["MA","amount",20],300000000]},{"name":"趋势与波动","conditions":[{"name":"60日波动率在后50%","expr":["LE",["CS_PCT",["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60]],0.5]},{"name":"站上60日均线","expr":["GT",["SUB",["DIV","adj_close",["MA","adj_close",60]],1],0]}],"mode":"any"}],"factors":[{"name":"动量","expr":["SUB",["DIV","adj_close",["REF","adj_close",20]],1]},{"name":"低波","expr":["STD",["SUB",["DIV","adj_close",["REF","adj_close",1]],1],60],"prefer":"low"}],"top_n":20,"backups":5,"hold_rank":30,"weighting":"score","max_weight":0.055}
JSON
# Relative to the repository root, where the CLI runs, so the commands shown on the page are portable.
S1="frontend/fixtures/runs-input/s1.json"
S2="frontend/fixtures/runs-input/s2.json"
SCREEN_PAST=2025-06-13
fixture screen/s1-last screen --strategy "$S1"
fixture screen/s1-past screen --strategy "$S1" --date "$SCREEN_PAST"
fixture screen/s2-last screen --strategy "$S2"
fixture backtest/s1 backtest --strategy "$S1" --start 2023-01-01
fixture backtest/s2 backtest --strategy "$S2" --start 2021-11-08
# The report run is not archived again (--no-save); the archived s1 run above is the one the runs commands read.
fixture backtest/s1-report backtest --strategy "$S1" --start 2023-01-01 --no-save --report
# An invalid request (start after end): backtest exits 2 with the ErrorBody on stdout.
mkdir -p "$OUT/backtest"
(cd "$REPO" && "$UV" run --frozen wequant backtest --strategy "$S1" --start 2026-01-01 --end 2025-01-01 --no-save \
  --home "$HOME_DIR" --cache "$CACHE_DIR" --json) >"$OUT/backtest/error.json" || true
python3 -c 'import json, sys; assert json.load(open(sys.argv[1]))["exit"] == 2' "$OUT/backtest/error.json"
python3 -c 'import json, sys; print(json.dumps({"name": "backtest/error", "args": sys.argv[1:]}))' \
  backtest --strategy "$S1" --start 2026-01-01 --end 2025-01-01 --no-save >>"$RUNS"
echo "fixtures/backtest/error.json"

run_id() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["run_id"])' "$OUT/backtest/$1.json"; }
R1="$(run_id s1)"
R2="$(run_id s2)"
fixture runs/list runs list
for r in "$R1" "$R2"; do
  fixture "runs/$r/show" runs show "$r"
  fixture "runs/$r/anomalies" runs anomalies "$r" --limit 500
done
# All orders (pages of 500; requested is null for a full exit, F-24) and the rejected ones as a filtered page.
total=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["orders"]["total"])' "$OUT/backtest/s1.json")
for ((off = 0; off < total; off += 500)); do fixture "runs/$R1/orders/$off" runs orders "$R1" --limit 500 --offset "$off"; done
fixture "runs/$R1/orders-rejected" runs orders "$R1" --status rejected --limit 500
fixture "runs/$R1/rerun" runs rerun "$R1"
# Days: the first three decision days (a fill day's previous trading day) and the first day with no decision.
days=$(python3 - "$OUT/runs/$R1/show.json" <<'PY'
import json, sys
daily = json.load(open(sys.argv[1]))["daily"]
# A decision day is one whose next day has fills: the 2nd, 10th and 30th of them (with sells), and a quiet day.
trades = lambda d: d["bought"] or d["sold"]
days = range(1, len(daily) - 1)
decisions = [daily[i]["date"] for i in days if trades(daily[i + 1])]
decisions = [decisions[k] for k in (1, 9, 29)]
quiet = next(daily[i]["date"] for i in days if not trades(daily[i]) and not trades(daily[i + 1]))
print(" ".join(decisions + [quiet]))
PY
)
for d in $days; do fixture "runs/$R1/day/$d" runs day "$R1" "$d"; done
fixture "runs/diff/$R1-$R2" runs diff "$R1" "$R2"

# explain on the s1 screen of the last day: the first target, the first backup and the first stock a condition
# rejected; plus one target of the first decision day of the s1 run (--run: what the run decided and filled). The
# first target also gets stock fixtures, so the side panel's mini chart has data.
read -r T B X < <(python3 - "$OUT/screen/s1-last.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
role = lambda r: next(x["code"] for x in d["rows"] if x["role"] == r)
print(role("target"), role("backup"), next(x["code"] for x in d["rejects"] if x["kind"] == "condition"))
PY
)
LAST=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["date"])' "$OUT/screen/s1-last.json")
for c in "$T" "$B" "$X"; do fixture "explain/s1-last/$c" explain --strategy "$S1" --code "$c" --date "$LAST"; done
D1=$(set -- $days; echo "$1")
RT=$(python3 -c 'import json, sys; print(next(x["code"] for x in json.load(open(sys.argv[1]))["decisions"] if x["role"] == "target"))' "$OUT/runs/$R1/day/$D1.json")
fixture "explain/run/$R1/$D1/$RT" explain --run "$R1" --code "$RT" --date "$D1"
stock "$T" "$SHORT_START"

# Keep the run log, so the index can be rewritten without running the CLI again:
#   node scripts/gen-fixture-index.mjs fixtures/runs.jsonl
cp "$RUNS" "$OUT/runs.jsonl"
node "$HERE/scripts/gen-fixture-index.mjs" "$OUT/runs.jsonl"
