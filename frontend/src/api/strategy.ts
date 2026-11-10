// Strategy results come from the backend only: validation, the summary sentence, weight shares and the content
// hash (D16). The browser cannot call the CLI, so until S5.1a the page shows real results only for the local example
// strategies whose output gen-fixtures.sh captured; any other draft reads "pending".
import { errorBody, type Fixture } from "./contract";
import { loadSource, type StrategyExample } from "./source";
import syntheticError from "./synthetic/strategy-validate-error.json";
import type { ErrorBody, StrategyCheck, StrategySpec, StrategySummary } from "./types.gen";

export type Backed<T> = { state: "pending" } | { state: "ok"; data: T } | { state: "error"; error: ErrorBody };

/** The local example whose strategy is exactly this draft (results belong to that content only). */
async function example(spec: StrategySpec): Promise<StrategyExample | null> {
  const key = JSON.stringify(spec);
  for (const ex of (await loadSource()).strategyExamples ?? []) {
    if (JSON.stringify(await ex.strategy.load()) === key) return ex;
  }
  return null;
}

/** `wequant strategy validate`: a StrategyCheck, the ErrorBody of an invalid strategy, or pending. */
export async function validateStrategy(spec: StrategySpec): Promise<Backed<StrategyCheck>> {
  const ex = await example(spec);
  if (ex?.error) return { state: "error", error: await ex.error.load() };
  if (ex?.validate) return { state: "ok", data: await ex.validate.load() };
  return { state: "pending" };
}

/** `wequant strategy summary`, or pending. */
export async function summarizeStrategy(spec: StrategySpec): Promise<Backed<StrategySummary>> {
  const ex = await example(spec);
  return ex?.summary ? { state: "ok", data: await ex.summary.load() } : { state: "pending" };
}

/** The local example strategies (none without local fixtures). */
export async function strategyExamples(): Promise<{ name: string; strategy: StrategySpec }[]> {
  return Promise.all(((await loadSource()).strategyExamples ?? []).map(async (ex) => ({ name: ex.name, strategy: await ex.strategy.load() })));
}

/** A synthetic ErrorBody (made up, D20): the fallback demonstration of error locating when there are no fixtures. */
export const exampleError: ErrorBody = errorBody(syntheticError satisfies Fixture<ErrorBody>, "strategy-validate-error.json");
