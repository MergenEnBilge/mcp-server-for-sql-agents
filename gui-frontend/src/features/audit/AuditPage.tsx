import { useEffect, useRef } from "react";

import { useApi } from "../../api/client";
import type { AdminLogItem, AuditFacets, AuditItem, Page } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { plural } from "../../components/format";
import { ErrorNote, PageHead, SegmentedControl } from "../../components/ui";
import { AdminLogTable } from "./AdminLogTable";
import { AuditFilters } from "./AuditFilters";
import { AuditTable } from "./AuditTable";
import { apiQuery, hasFilters, PAGE_SIZE, useAuditParams, type SortKey } from "./params";

/** The screen people will use most: everything AI callers did, filterable down to one call. */
export function AuditPage() {
  const api = useApi();
  const [params, update, clearFilters] = useAuditParams();
  const searchRef = useRef<HTMLInputElement>(null);

  const facets = useAsync((signal) => api.get<AuditFacets>("/audit/facets", undefined, signal), [api]);
  const query = JSON.stringify(apiQuery(params));
  const calls = useAsync(
    (signal) => (params.view === "calls" ? api.get<Page<AuditItem>>("/audit", apiQuery(params), signal) : Promise.resolve(undefined)),
    [api, params.view, query],
  );
  const admin = useAsync(
    (signal) =>
      params.view === "admin"
        ? api.get<Page<AdminLogItem>>("/admin-log", { page: params.page, page_size: PAGE_SIZE, actor: params.user, date_from: apiQuery(params).date_from, date_to: apiQuery(params).date_to }, signal)
        : Promise.resolve(undefined),
    [api, params.view, params.page, params.user, params.from, params.to],
  );

  // "/" jumps to search, the way it does in most log tools.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(tag)) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const active = params.view === "calls" ? calls : admin;
  const page = (active.data ?? undefined) as Page<AuditItem | AdminLogItem> | undefined;
  const lastPage = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;
  const from = page && page.total ? (params.page - 1) * PAGE_SIZE + 1 : 0;
  const to = page ? Math.min(page.total, params.page * PAGE_SIZE) : 0;

  function sortBy(key: SortKey) {
    update(key === params.sort ? { order: params.order === "asc" ? "desc" : "asc" } : { sort: key, order: key === "occurred_at" ? "desc" : "asc" });
  }

  return (
    <>
      <PageHead
        title="Audit log"
        sub={
          params.view === "calls"
            ? "Every call an AI made through the data layer, including the ones that were refused."
            : "Every change an administrator made in this console."
        }
        actions={
          <SegmentedControl
            label="Which log"
            value={params.view}
            options={[
              { value: "calls", label: "Tool calls" },
              { value: "admin", label: "Admin changes" },
            ]}
            onChange={(view) => update({ view, sort: "occurred_at", order: "desc" })}
          />
        }
      />

      {params.view === "calls" ? (
        <AuditFilters params={params} facets={facets.data} onChange={update} onClear={clearFilters} searchRef={searchRef} />
      ) : (
        <form className="filters" role="search" aria-label="Filter admin changes" onSubmit={(e) => e.preventDefault()}>
          <input className="input" type="search" aria-label="Administrator" placeholder="Administrator name or id" value={params.user} onChange={(e) => update({ user: e.target.value })} />
        </form>
      )}

      {active.error ? (
        <ErrorNote onRetry={active.reload}>{active.error}</ErrorNote>
      ) : !page ? (
        <p className="muted" role="status">
          Loading the log…
        </p>
      ) : page.items.length === 0 ? (
        <div className="empty" role="status">
          {hasFilters(params) || params.user
            ? params.view === "calls"
              ? "No audit entries match these filters."
              : "No admin changes match this."
            : params.view === "calls"
              ? "Nothing has been logged yet. Calls made through the MCP server will appear here."
              : "No changes have been made in this console yet."}{" "}
          {hasFilters(params) && (
            <button type="button" className="btn btn-quiet" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      ) : params.view === "calls" ? (
        <AuditTable items={page.items as AuditItem[]} params={params} onSort={sortBy} />
      ) : (
        <AdminLogTable items={page.items as AdminLogItem[]} />
      )}

      {page && page.total > 0 && (
        <nav className="pager" aria-label="Pages">
          <span role="status" aria-live="polite">
            {active.loading ? "Loading…" : `${from.toLocaleString("en-GB")}–${to.toLocaleString("en-GB")} of ${plural(page.total, "entry", "entries")}`}
          </span>
          <div className="btns">
            <button type="button" className="btn" disabled={params.page <= 1} onClick={() => update({ page: params.page - 1 })}>
              Previous
            </button>
            <button type="button" className="btn" disabled={params.page >= lastPage} onClick={() => update({ page: params.page + 1 })}>
              Next
            </button>
          </div>
        </nav>
      )}
    </>
  );
}
