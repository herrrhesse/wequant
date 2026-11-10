import { useQuery } from "@tanstack/react-query";
import { dataIssues, dataStatus, isSynthetic, issueFilters, statusVersions, stockCodes, stockData, type IssuesFilter } from "./client";

export function useDataStatus(version: number | null) {
  return useQuery({ queryKey: ["data", "status", version], queryFn: () => dataStatus(version) });
}

export function useStatusVersions() {
  return useQuery({ queryKey: ["data", "status-versions"], queryFn: statusVersions });
}

export function useDataIssues(filter: IssuesFilter) {
  return useQuery({ queryKey: ["data", "issues", filter.table, filter.kind, filter.code ?? null], queryFn: () => dataIssues(filter) });
}

export function useStock(code: string) {
  return useQuery({ queryKey: ["stock", code], queryFn: () => stockData(code) });
}

export function useIssueFilters() {
  return useQuery({ queryKey: ["data", "issue-filters"], queryFn: issueFilters });
}

export function useSynthetic() {
  return useQuery({ queryKey: ["source", "synthetic"], queryFn: isSynthetic });
}

export function useStockCodes() {
  return useQuery({ queryKey: ["stock", "codes"], queryFn: stockCodes });
}
