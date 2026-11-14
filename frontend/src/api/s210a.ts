// S2.10a (剔除退市整理期) as the backend named it before its schema snapshot has it. TEMPORARY: removed once main
// carries S2.10a and npm run gen:types writes these fields into types.gen.ts.
import type { Label } from "./types.gen";

declare module "./types.gen" {
  interface PoolSpec {
    /** Leave out stocks in their last trading days before delisting (short name with 退); default off when absent. */
    exclude_delisting?: boolean;
  }
  interface ScopeStatus {
    /** Each index of the oldest layer with its first member-list day (empty when that layer holds every stock). */
    member_floors?: BackfillFloor[];
  }
  interface BuildEstimate {
    /** The same for the scope of this build. */
    member_floors?: BackfillFloor[];
  }
  interface Reject {
    /** A scope step's sentence, as the judgment chain's (PoolCheck.note); null: none. */
    note?: string | null;
  }
  interface Labels {
    /** Each scope item's short name (funnel, judgment chain) and its sentence (the strategy page's row). */
    pool_items?: Label[];
  }
}

export type PoolItemLabel = Label;
