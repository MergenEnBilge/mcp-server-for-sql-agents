import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { endOfDayExclusive, startOfDay } from "../../components/format";

export type SortKey = "occurred_at" | "caller" | "tool" | "connection" | "duration" | "rows" | "status";
export const SORT_KEYS: SortKey[] = ["occurred_at", "caller", "tool", "connection", "duration", "rows", "status"];

export interface AuditParams {
  view: "calls" | "admin";
  user: string;
  tool: string;
  connection: string;
  table: string;
  outcome: "" | "ok" | "failed";
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD, inclusive
  q: string;
  sort: SortKey;
  order: "asc" | "desc";
  page: number;
}

const DEFAULTS: AuditParams = {
  view: "calls",
  user: "",
  tool: "",
  connection: "",
  table: "",
  outcome: "",
  from: "",
  to: "",
  q: "",
  sort: "occurred_at",
  order: "desc",
  page: 1,
};

/** Filters, sort and page live in the address bar, so a view can be shared and survives a reload. */
export function useAuditParams(): [AuditParams, (change: Partial<AuditParams>) => void, () => void] {
  const [search, setSearch] = useSearchParams();

  const params = useMemo<AuditParams>(() => {
    const get = (key: string) => search.get(key) ?? "";
    const sort = get("sort") as SortKey;
    const outcome = get("outcome");
    return {
      view: get("view") === "admin" ? "admin" : "calls",
      user: get("user"),
      tool: get("tool"),
      connection: get("connection"),
      table: get("table"),
      outcome: outcome === "ok" || outcome === "failed" ? outcome : "",
      from: get("from"),
      to: get("to"),
      q: get("q"),
      sort: SORT_KEYS.includes(sort) ? sort : DEFAULTS.sort,
      order: get("order") === "asc" ? "asc" : "desc",
      page: Math.max(1, Number(get("page")) || 1),
    };
  }, [search]);

  const update = useCallback(
    (change: Partial<AuditParams>) => {
      const next = { ...params, ...change };
      // Anything other than paging itself sends you back to the first page.
      if (!("page" in change)) next.page = 1;
      const out = new URLSearchParams();
      for (const [key, value] of Object.entries(next)) {
        const isDefault = value === (DEFAULTS as unknown as Record<string, unknown>)[key];
        if (!isDefault && value !== "") out.set(key, String(value));
      }
      setSearch(out, { replace: true });
    },
    [params, setSearch],
  );

  const clearFilters = useCallback(() => update({ user: "", tool: "", connection: "", table: "", outcome: "", from: "", to: "", q: "" }), [update]);
  return [params, update, clearFilters];
}

export const PAGE_SIZE = 50;

/** The query string the API expects for the current view. */
export function apiQuery(p: AuditParams): Record<string, string | number | boolean | undefined> {
  return {
    user: p.user,
    tool: p.tool,
    connection: p.connection,
    table: p.table,
    success: p.outcome === "" ? undefined : p.outcome === "ok",
    date_from: p.from ? startOfDay(p.from) : undefined,
    date_to: p.to ? endOfDayExclusive(p.to) : undefined,
    q: p.q,
    sort: p.sort,
    order: p.order,
    page: p.page,
    page_size: PAGE_SIZE,
  };
}

export function hasFilters(p: AuditParams): boolean {
  return Boolean(p.user || p.tool || p.connection || p.table || p.outcome || p.from || p.to || p.q);
}
