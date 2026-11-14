// The setup's steps (SetupPage holds the state and the jobs).
import { useState } from "react";
import { useCall, useCallMutation } from "../../api/client";
import { downloadText, size } from "../../api/files";
import { finished } from "../../api/jobs";
import type { BuildResult, OwnImported } from "../../api/types.gen";
import { FolderPicker, useFolder } from "../../components/FolderPicker";
import { about, fillYears, JobBar } from "../../components/JobBar";
import { Problem } from "../../components/Problem";
import { useLabels } from "../../labels";
import { href } from "../../router";
import { facts, startDraft } from "../strategy/NewStrategy";
import { useScopes } from "../../components/IndexSeg";
import { useQueryClient } from "@tanstack/react-query";
import {
  type Ctx,
  JobFailure,
  type Source,
  SOURCE_NAMES,
  type Years,
} from "./SetupPage";
import { BARS_EXAMPLE, SECURITIES_EXAMPLE } from "./examples";
import { type Pick, ScopeChoice, scopeOf, useEstimate } from "./Scope";
import { show } from "../../format";
import { historyStop, stopNote, stopYear } from "../../components/history";

const SOURCES: { source: Source; desc: string }[] = [
  {
    source: "baostock",
    desc: "先建最近几年，建好马上能用；更早的历史在后台补全，可以暂停。",
  },
  { source: "import", desc: "已经有日线等文件？先检查格式和缺失，再导入。" },
  { source: "existing", desc: "电脑上已经有一份 WeQuant 数据？直接用它。" },
];

export function SourceStep(props: {
  ctx: Ctx;
  setSource: (s: Source) => void;
  setYears: (y: Years) => void;
  setBackfill: (b: boolean) => void;
  setFolder: (f: string) => void;
  setPick: (p: Pick) => void;
  busy: boolean;
}) {
  const { ctx } = props;
  const { status, source, years, backfill, folder, pick } = ctx;
  const client = useQueryClient();
  const [picking, setPicking] = useState(false);
  const here = useFolder(folder);
  const scope = scopeOf(pick);
  const estimate = useEstimate(years, scope, source === "baostock");
  // Data downloaded by index goes back to its member lists only (the backend's days for this scope).
  const buildStop = historyStop(estimate.data?.member_floors);
  const useHome = useCallMutation("data_use_home");
  const current = folder === status.home;
  // Building and importing go into an empty or new folder (the backend switches to it, making it when missing); a
  // folder with WeQuant data is used as it is. A folder that does not exist yet has no listing.
  const missing = here.listing.isError;
  const why = (s: Source): string => {
    if (!here.ready && !missing) return "";
    if (s === "existing")
      return here.isHome
        ? ""
        : "选的文件夹里没有 WeQuant 数据：用上面的“浏览…”选一个有数据的文件夹。";
    if (here.isHome || (current && status.has_data))
      return "这个文件夹里已经有数据；新建或导入需要一个空的文件夹，可以用“浏览…”新建一个。";
    if (s === "baostock" && pick.choice === "stocks" && !pick.poolName.trim())
      return "给名单起个名字，建好后存成股票池。";
    return "";
  };
  const blocked = why(source);
  const next = async () => {
    if (!current) {
      await useHome.mutateAsync(
        source === "existing"
          ? { path: folder }
          : { path: folder, empty_ok: true },
      );
      void client.invalidateQueries();
    }
    if (source === "existing") ctx.go("ready", { source, home: folder });
    else if (source === "baostock")
      await ctx.start("data_build", ctx.buildRequest(), "build", {
        source,
        years,
        backfill,
        home: folder,
      });
    else ctx.go("import_folder", { source, home: folder });
  };
  return (
    <div className="col setup-body">
      <span className="b">数据放在哪里</span>
      <div className="row nowrap">
        <span
          className="input grow path setup-path"
          translate="no"
          aria-label="数据文件夹"
        >
          {here.listing.data?.path ?? folder}
        </span>
        <button
          type="button"
          className="btn soft"
          onClick={() => setPicking(true)}
        >
          浏览…
        </button>
      </div>
      <span className="small muted">
        {here.listing.data
          ? `所在磁盘还有 ${size(here.listing.data.free_bytes)} 可用。`
          : ""}
        {here.isHome
          ? `这里已经有一份 WeQuant 数据${here.entry?.size_bytes != null ? `（${size(here.entry.size_bytes)}）` : ""}。`
          : ""}
        {current
          ? "这是现在的数据文件夹。"
          : missing
            ? "这个文件夹还不存在，下一步时会新建。"
            : !here.isHome && here.ready
              ? "下一步时改用这个文件夹。"
              : ""}
      </span>
      <span className="b">数据从哪里来</span>
      <div className="list-grid setup-sources">
        {SOURCES.map((o) => (
          <button
            key={o.source}
            type="button"
            className="inner tpl-card"
            aria-pressed={source === o.source}
            onClick={() => props.setSource(o.source)}
          >
            <span className="b">{SOURCE_NAMES[o.source]}</span>
            <span className="small sub">{o.desc}</span>
          </button>
        ))}
      </div>
      {source === "baostock" && (
        <div className="inner col">
          <span className="b">下载哪些股票</span>
          <ScopeChoice years={years} pick={pick} setPick={props.setPick} />
          <div className="row">
            <span className="small">建最近</span>
            <div className="seg" role="group" aria-label="建最近几年">
              {([1, 3, 5] as const).map((y) => (
                <button
                  key={y}
                  type="button"
                  aria-pressed={years === y}
                  onClick={() => props.setYears(y)}
                >
                  {y} 年
                </button>
              ))}
            </div>
            {estimate.data && scope && (
              <span className="small sub" role="status" aria-label="预计耗时">
                合计约 {show(estimate.data.stocks)} 只股票、{show(estimate.data.days)} 个交易日，预计
                {about(estimate.data.seconds)}
                {estimate.data.already > 0
                  ? `；上次已经下载了 ${estimate.data.already} 只股票，这次不用重下`
                  : ""}
              </span>
            )}
          </div>
          <label className="row small">
            <input
              type="checkbox"
              checked={backfill}
              onChange={(e) => props.setBackfill(e.target.checked)}
            />
            建好后在后台补齐到 {stopYear(buildStop)} 年（可以随时暂停）
          </label>
          {backfill && buildStop && (
            <span className="tiny muted" role="note" aria-label="补齐能补到哪天">
              {stopNote(buildStop)}
            </span>
          )}
          <span className="tiny muted">
            BaoStock 一次只允许一个连接，建库期间别在别处登录 BaoStock。
          </span>
        </div>
      )}
      {blocked && <span className="small warn-text">{blocked}</span>}
      {useHome.isError && <Problem error={useHome.error} />}
      <div className="row">
        <button
          type="button"
          className="btn acc push"
          disabled={
            (!here.ready && !missing) ||
            !!blocked ||
            (source === "baostock" && !scope) ||
            props.busy ||
            useHome.isPending
          }
          onClick={() => void next().catch(() => undefined)}
        >
          {source === "existing" ? "使用这份数据" : "下一步"}
        </button>
      </div>
      {picking && (
        <FolderPicker
          title="选择数据存放的文件夹"
          start={folder}
          openParent
          current={status.home}
          onClose={() => setPicking(false)}
          onChoose={(p) => {
            setPicking(false);
            props.setFolder(p);
          }}
        />
      )}
    </div>
  );
}

export function BuildStep({
  ctx,
  back,
  result,
}: {
  ctx: Ctx;
  back: () => void;
  result: BuildResult | null;
}) {
  const L = useLabels();
  const { job, years } = ctx;
  const resume = () =>
    void ctx.start("data_build", ctx.buildRequest(), "build").catch(() => undefined);
  const running = !!job && !finished(job);
  return (
    <div className="col setup-body">
      {job && running && (
        <JobBar job={job} label={`用 BaoStock 建最近 ${years} 年的数据`}>
          <button type="button" className="btn soft" onClick={back}>
            ‹ 上一步
          </button>
          <button type="button" className="btn soft" onClick={ctx.stop}>
            暂停
          </button>
        </JobBar>
      )}
      {running && (
        <span className="small muted">
          可以离开这个页面，建好后回到这里就能看到。回到上一步会暂停，已下载的部分保留。
        </span>
      )}
      {!running && (
        <>
          <span className="b">用 BaoStock 建最近 {years} 年的数据</span>
          {job?.state === "cancelled" && (
            <span className="small">
              已暂停。已下载的部分保留，继续时接着下载。
            </span>
          )}
          {job?.state === "failed" && <JobFailure job={job} />}
          {job?.state === "succeeded" && result && (
            <span
              className={
                result.status === "built" || result.status === "exists"
                  ? "small ok b"
                  : "small warn-text"
              }
            >
              {L.of("build_statuses", result.status)}
              {result.first && result.last
                ? `：${result.first} 至 ${result.last}，${show(result.days)} 个交易日`
                : `：${L.help("build_statuses", result.status)}`}
            </span>
          )}
          {!job && ctx.status.has_data && (
            <span className="small ok b">数据已经建好。</span>
          )}
          <div className="row">
            <button type="button" className="btn soft" onClick={back}>
              ‹ 上一步
            </button>
            {(!job ||
              job.state === "cancelled" ||
              job.state === "failed" ||
              (job.state === "succeeded" &&
                result?.status === "not_published")) &&
              !ctx.status.has_data && (
                <button type="button" className="btn acc" onClick={resume}>
                  {job?.state === "cancelled"
                    ? "继续"
                    : job
                      ? "再试一次"
                      : "开始"}
                </button>
              )}
          </div>
        </>
      )}
    </div>
  );
}

export function HistoryStep({ ctx, back }: { ctx: Ctx; back: () => void }) {
  const { job, years } = ctx;
  const running = !!job && job.kind === "data_backfill" && !finished(job);
  const stop = historyStop(useCall("data_status", ctx.status.has_data ? {} : null).data?.scope?.member_floors);
  const resume = () =>
    void ctx.start("data_backfill", {}, "history").catch(() => undefined);
  return (
    <div className="col setup-body">
      <span className="small ok b">
        最近 {years} 年的数据已经可用：现在就能选股和回测。
      </span>
      {job && running ? (
        <JobBar
          job={job}
          label={`后台补全更早的历史（到 ${stopYear(stop)} 年）${fillYears(job) ? ` · 正在补 ${fillYears(job)}` : ""}`}
        >
          <button
            type="button"
            className="btn soft"
            onClick={() => void ctx.stop()}
          >
            暂停
          </button>
        </JobBar>
      ) : (
        <span className="small">
          {job?.state === "succeeded"
            ? `更早的历史已经补齐到 ${stop ? stop.stop.date : "2000 年"}。`
            : job?.state === "failed"
              ? ""
              : "补全更早的历史已暂停，已经补上的部分保留。"}
        </span>
      )}
      {job?.state === "failed" && <JobFailure job={job} />}
      <div className="row">
        <button type="button" className="btn soft" onClick={back}>
          ‹ 上一步
        </button>
        {!running && job?.state !== "succeeded" && (
          <button type="button" className="btn soft" onClick={resume}>
            继续补全
          </button>
        )}
        <button
          type="button"
          className="btn acc push"
          onClick={() => ctx.go("ready")}
        >
          {running ? "先去用，让它在后台跑" : "下一步"}
        </button>
      </div>
      <span className="tiny muted">
        补全的进度在顶部状态栏和数据页也能看到，可以随时暂停和继续。
      </span>
    </div>
  );
}

export function ImportFolderStep({
  ctx,
  back,
  setImportPath,
}: {
  ctx: Ctx;
  back: () => void;
  setImportPath: (p: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const path = ctx.importPath;
  return (
    <div className="col setup-body">
      <span className="b">选择要导入的数据文件夹</span>
      <div className="row nowrap">
        <input
          className="input grow path"
          translate="no"
          aria-label="要导入的文件夹"
          placeholder="例如 /home/我/下载/A股日线"
          value={path}
          onChange={(e) => setImportPath(e.target.value)}
        />
        <button
          type="button"
          className="btn soft"
          onClick={() => setPicking(true)}
        >
          浏览…
        </button>
      </div>
      <Format />
      <div className="row">
        <button type="button" className="btn soft" onClick={back}>
          ‹ 上一步
        </button>
        <button
          type="button"
          className="btn acc push"
          disabled={!path.trim()}
          onClick={() => ctx.go("import_check", { import_path: path.trim() })}
        >
          检查这个文件夹
        </button>
      </div>
      {picking && (
        <FolderPicker
          title="选择要导入的数据文件夹"
          start={path.trim() || ctx.status.home}
          openParent={!path.trim()}
          chooseLabel="选择这个文件夹"
          onClose={() => setPicking(false)}
          onChoose={(p) => {
            setPicking(false);
            setImportPath(p);
          }}
        />
      )}
    </div>
  );
}

/** The own-data format (the backend's README) in plain words, with example files. */
function Format() {
  const col = (name: string) => (
    <code className="path" translate="no">
      {name}
    </code>
  );
  return (
    <details className="inner setup-format" open>
      <summary className="small b">文件要求</summary>
      <div className="col">
        <span className="small">
          文件夹里放一个日线文件 {col("bars.csv")}（也可以是{" "}
          {col("bars.parquet")}
          ），每一行是一只股票一天的数据，第一行写列名。必须有这几列：
        </span>
        <ul className="small">
          <li>
            {col("code")}：股票代码，带交易所后缀，例如 600000.SH、000001.SZ
          </li>
          <li>{col("date")}：日期，写成 2025-01-02 这样</li>
          <li>
            {col("open")}、{col("high")}、{col("low")}、{col("close")}
            ：开盘价、最高价、最低价、收盘价，单位元，不复权
          </li>
          <li>
            {col("volume")}：成交量，单位股；{col("amount")}：成交额，单位元
          </li>
        </ul>
        <span className="small">
          可以有的列：{col("preclose")}（前收盘价，没有就用上一天的收盘价）、
          {col("factor")}（后复权因子）、{col("st")}（这天是不是 ST，1 是 0
          不是）、{col("tradestatus")}
          （1 正常交易，0 停牌）。
        </span>
        <span className="small">
          还可以放一个股票列表 {col("securities.csv")}：{col("code")}、
          {col("name")}（名称）、{col("list_date")}（上市日期）、
          {col("delist_date")}
          （退市日期，没退市就空着）。没有它，名称会空着，上市天数从每只股票的第一根
          K 线算起。
        </span>
        <span className="small sub">
          导入前会先检查，只读不写；读不出来的行、重复的行不会导入，价格对不上的行照原样导入并标出来，都会列在数据页。
        </span>
        <div className="row">
          <button
            type="button"
            className="btn sm soft"
            onClick={() => downloadText("bars.csv", BARS_EXAMPLE)}
          >
            下载示例 bars.csv
          </button>
          <button
            type="button"
            className="btn sm soft"
            onClick={() => downloadText("securities.csv", SECURITIES_EXAMPLE)}
          >
            下载示例 securities.csv
          </button>
        </div>
      </div>
    </details>
  );
}

const OPTIONAL: Record<string, string> = {
  preclose: "前收盘价",
  factor: "复权因子",
  st: "ST 标记",
  tradestatus: "交易状态",
};

export function ImportCheckStep({
  ctx,
  back,
  busy,
}: {
  ctx: Ctx;
  back: () => void;
  busy: boolean;
}) {
  const L = useLabels();
  const check = useCall(
    "data_own_check",
    { path: ctx.importPath },
    { staleTime: 0 },
  );
  const c = check.data;
  const row = (ok: boolean, label: string, detail: string) => (
    <div className={`setup-check ${ok ? "ok" : "warn"}`}>
      <span className="setup-mark" aria-hidden="true">
        {ok ? "✓" : "!"}
      </span>
      <span className="small">
        <b>{label}</b> <span className="sub">{detail}</span>
      </span>
    </div>
  );
  return (
    <div className="col setup-body">
      <span className="b">
        检查结果 ·{" "}
        <span className="path" translate="no">
          {ctx.importPath}
        </span>
      </span>
      {check.isPending && <span className="note">正在检查，只读不写…</span>}
      {check.isError && <Problem error={check.error} />}
      {c && (
        <div className="col tight" aria-label="检查结果">
          {row(
            !!c.bars_file,
            "日线",
            c.bars_file
              ? `${c.codes} 只股票，${c.rows} 行，${c.first ?? ""} 至 ${c.last ?? ""}`
              : "没有找到 bars.csv 或 bars.parquet",
          )}
          {row(
            !!c.securities_file,
            "股票列表",
            c.securities_file
              ? "有名称和上市日期"
              : "没有 securities.csv：名称会空着，上市天数从第一根 K 线算起",
          )}
          {row(
            c.optional.length > 0,
            "可选的列",
            c.optional.length
              ? `有${c.optional.map((o) => OPTIONAL[o] ?? o).join("、")}`
              : "没有前收盘价、复权因子等可选的列，前收盘价按上一天收盘价算",
          )}
          {c.problems.map((p) => (
            <div key={p.rule} className="setup-check warn">
              <span className="setup-mark" aria-hidden="true">
                !
              </span>
              <span className="small">
                <b>{L.of("own_rules", p.rule)}</b>{" "}
                <span className="sub">
                  {p.rule === "missing_columns"
                    ? `缺 ${p.examples.map((e) => e[0]).join("、")}`
                    : p.rule === "no_list_date"
                      ? `${p.rows} 只股票`
                      : `${p.rows} 行`}
                  ：{L.help("own_rules", p.rule)}
                </span>
                {p.rule !== "missing_columns" && p.examples.length > 0 && (
                  <span className="tiny muted path">
                    {" "}
                    例如{" "}
                    {p.examples
                      .map((e) => e.filter(Boolean).join(" "))
                      .join("；")}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      {c && !c.ok && (
        <span className="small warn-text">
          这个文件夹还不能导入：先按上面的说明改好文件。
        </span>
      )}
      {c?.ok && (
        <span className="small sub">
          导入后可以用“更新到今天”从 BaoStock
          补到最新；发现的问题会列在数据页，由你决定怎么处理。
        </span>
      )}
      <div className="row">
        <button type="button" className="btn soft" onClick={back}>
          ‹ 换一个文件夹
        </button>
        <button
          type="button"
          className="btn acc push"
          disabled={!c?.ok || busy}
          onClick={() =>
            void ctx
              .start("data_own_import", { path: ctx.importPath }, "import")
              .catch(() => undefined)
          }
        >
          开始导入
        </button>
      </div>
    </div>
  );
}

export function ImportStep({
  ctx,
  back,
  result,
}: {
  ctx: Ctx;
  back: () => void;
  result: OwnImported | null;
}) {
  const { job } = ctx;
  const running = !!job && !finished(job);
  return (
    <div className="col setup-body">
      {job && running && <JobBar job={job} label="正在导入" />}
      {job?.state === "succeeded" && result && (
        <span className="small ok b" role="status">
          导入完成：{show(result.codes)} 只股票、{show(result.days)} 个交易日，数据版本{" "}
          {result.version_name}
          {result.issues > 0
            ? `；有 ${result.issues} 条没导入或标出来的行，列在数据页`
            : ""}
          。
        </span>
      )}
      {job?.state === "cancelled" && (
        <span className="small">已停止导入，没有写入任何数据。</span>
      )}
      {job?.state === "failed" && <JobFailure job={job} />}
      <div className="row">
        <button type="button" className="btn soft" onClick={back}>
          {running ? "‹ 上一步（停止导入）" : "‹ 上一步"}
        </button>
        {job?.state === "succeeded" && (
          <button
            type="button"
            className="btn acc push"
            onClick={() => ctx.go("ready")}
          >
            下一步
          </button>
        )}
      </div>
    </div>
  );
}

export function ReadyStep({ ctx, back }: { ctx: Ctx; back: () => void }) {
  const templates = useCall("strategy_templates", {});
  const { name } = useScopes();
  const save = useCallMutation("setup_save");
  const finish = async () => {
    await save.mutateAsync({ clear: true }).catch(() => undefined);
  };
  return (
    <div className="col setup-body">
      <h2 className="setup-h2">准备好了</h2>
      <span className="small sub">
        从一个模板新建策略，改几个数就能看到选股和回测结果。
      </span>
      <div className="list-grid">
        {(templates.data?.rows ?? []).map((t) => (
          <button
            key={t.id}
            type="button"
            className="inner tpl-card"
            aria-label={`模板：${t.name}`}
            onClick={async () => {
              await finish();
              startDraft(t.strategy, t.name);
            }}
          >
            <span className="b">{t.name}</span>
            <span className="small sub">{t.description}</span>
            <span className="tpl-facts">
              {facts(t.strategy, name).map((f) => (
                <span key={f} className="tag">
                  {f}
                </span>
              ))}
            </span>
          </button>
        ))}
      </div>
      <div className="row">
        <button type="button" className="btn soft" onClick={back}>
          ‹ 上一步
        </button>
        <button
          type="button"
          className="btn ghost push"
          onClick={async () => {
            await finish();
            location.hash = href.today();
          }}
        >
          先不用，去今日页 ›
        </button>
      </div>
      {ctx.job && ctx.job.kind === "data_backfill" && !finished(ctx.job) && (
        <span className="tiny muted">
          更早的历史还在后台补全，进度在顶部状态栏。
        </span>
      )}
    </div>
  );
}
