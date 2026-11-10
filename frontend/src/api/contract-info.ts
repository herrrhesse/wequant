// Facts read from the backend's schema snapshot, so the UI never hard-codes them.
import contract from "../../contract/service-models.schema.json";

interface Operation {
  operation: string;
  available: boolean;
}

/** Whether a service operation exists yet; planned ones have fixed models but no command (available = false). */
export function operationAvailable(name: string): boolean {
  return (contract.operations as Operation[]).some((o) => o.operation === name && o.available);
}

/** The panel fields an expression can read, in the snapshot's order (BarsRequest.fields). */
export function panelFields(): string[] {
  return contract.$defs.BarsRequest.properties.fields.items.enum;
}

/** The index codes pool.index accepts, in the snapshot's order. */
export function poolIndexCodes(): string[] {
  const index = contract.$defs.PoolSpec.properties.index.anyOf[0] as { enum?: string[] };
  return index.enum ?? [];
}
