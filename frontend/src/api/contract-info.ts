// Facts read from the backend's schema snapshot, so the UI never hard-codes them (picked out of the snapshot by
// npm run gen:types into contract.gen.ts: the page does not carry the whole snapshot).
import { AVAILABLE_OPERATIONS, PANEL_FIELDS, POOL_INDEX_CODES } from "./contract.gen";

/** Whether a service operation exists yet; planned ones have fixed models but no command (available = false). */
export function operationAvailable(name: string): boolean {
  return AVAILABLE_OPERATIONS.includes(name);
}

/** The panel fields an expression can read, in the snapshot's order (BarsRequest.fields). */
export function panelFields(): string[] {
  return [...PANEL_FIELDS];
}

/** The index codes pool.index accepts, in the snapshot's order. */
export function poolIndexCodes(): string[] {
  return [...POOL_INDEX_CODES];
}
