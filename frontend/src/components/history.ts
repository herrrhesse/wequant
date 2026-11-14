// How far back the history backfill goes. Data downloaded by index stops at the first member list of its indexes
// (the latest of their first days: before it, one index has no members, so the layer could not be complete); data
// holding every stock goes back to 2000. The days are the backend's (member_floors).
import type { BackfillFloor } from "../api/types.gen";

export interface HistoryStop {
  /** The index whose member list starts last: where the backfill stops. */
  stop: BackfillFloor;
  others: BackfillFloor[];
}

export function historyStop(floors: BackfillFloor[] | null | undefined): HistoryStop | null {
  if (!floors?.length) return null;
  const stop = floors.reduce((a, b) => (b.date > a.date ? b : a));
  return { stop, others: floors.filter((f) => f !== stop) };
}

/** The year the backfill goes back to: 2000, or the year of the member list that stops it. */
export const stopYear = (h: HistoryStop | null) => (h ? h.stop.date.slice(0, 4) : "2000");

/** Said beside the backfill, before it starts. */
export function stopNote(h: HistoryStop): string {
  const others = h.others.map((o) => `；${o.name}从 ${o.date} 开始`).join("");
  return `按指数下载的数据最早补到 ${h.stop.date}（${h.stop.name}的成分名单从这天开始${others}），更早需要扩大到沪深全部`;
}
