// Interface wording (Chinese only). Backend codes are never translated here: their labels come from `labels`.

export const tx = {
  app: { brand: "WeQuant", loading: "正在读取…" },
  nav: { label: "主导航", today: "今日", strategy: "策略", picks: "选股", backtest: "回测", data: "数据" },
  search: { label: "搜索股票", placeholder: "代码或名称，如 600036 或 招商", none: "没有匹配的股票" },
  prefs: {
    conv: "涨跌颜色",
    cn: "红涨绿跌",
    intl: "绿涨红跌",
    theme: "外观",
    light: "浅色",
    dark: "深色",
    system: "跟随系统",
    dev: "开发者模式",
    devOn: "开发者模式：开",
    devTitle: "显示复制命令、内容哈希、版本号和 JSON 视图",
  },
  status: {
    label: "数据状态",
    latest: "本地最新交易日",
    version: (name: string) => `版本 ${name}`,
    pending: (n: number) => `待处理的数据问题 ${n} ›`,
  },
  boards: { main: "主板", chinext: "创业板", star: "科创板" } as Record<string, string>,
  weekdays: ["周日", "周一", "周二", "周三", "周四", "周五", "周六"],
  common: { copy: "复制", copied: "已复制 ✓", none: "—", retry: "重试", close: "关闭", cancel: "取消" },
};

/** "周三" of an ISO date (display only). */
export function weekday(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? "" : (tx.weekdays[d.getUTCDay()] ?? "");
}

/** "11月11日" of an ISO date. */
export function cnDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return m && d ? `${Number(m)}月${Number(d)}日` : iso;
}
