import { t } from "../i18n";

/** wequant.service.Exit codes and the message the UI shows for each. */
export const EXIT = { OK: 0, PROBLEMS: 1, USAGE: 2, NOT_FOUND: 3, UNREACHABLE: 4, LOCKED: 5, INTERNAL: 70, INTERRUPTED: 130 } as const;

export type ExitCode = (typeof EXIT)[keyof typeof EXIT];

export class ServiceError extends Error {
  constructor(
    readonly exit: ExitCode,
    message: string,
  ) {
    super(message);
  }
}

export function exitMessage(exit: ExitCode): string {
  return t.exit[exit];
}
