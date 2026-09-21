import { Fragment, useState, type KeyboardEvent } from "react";

import { useApi } from "../../api/client";
import type { AuditDetail, AuditItem } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatFull, formatTimestamp } from "../../components/format";
import { ErrorNote } from "../../components/ui";
import type { AuditParams, SortKey } from "./params";

const COLUMNS: { key: SortKey | null; label: string; className?: string }[] = [
  { key: "occurred_at", label: "Time" },
  { key: "status", label: "Outcome" },
  { key: "caller", label: "Caller" },
  { key: "tool", label: "Tool" },
  { key: "connection", label: "Database" },
  { key: null, label: "Tables" },
  { key: null, label: "SQL" },
  { key: "rows", label: "Rows", className: "num" },
  { key: "duration", label: "Duration", className: "num" },
];

/** Bar length as a percentage of the slowest call on the page; none at all for the fast ones,
 * where a speck would only look like a rendering glitch. */
export function barWidth(ms: number, slowest: number): number {
  const percent = (ms / slowest) * 100;
  return percent < 4 ? 0 : Math.round(percent);
}

export function AuditTable({
  items,
  params,
  onSort,
}: {
  items: AuditItem[];
  params: AuditParams;
  onSort: (key: SortKey) => void;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const slowest = Math.max(1, ...items.map((i) => i.duration_ms));

  function onRowKey(event: KeyboardEvent<HTMLTableRowElement>, id: number) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpenId((current) => (current === id ? null : id));
    } else if (event.key === "Escape") {
      setOpenId(null);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const rows = Array.from(event.currentTarget.closest("tbody")?.querySelectorAll<HTMLTableRowElement>("tr.row") ?? []);
      const next = rows[rows.indexOf(event.currentTarget) + (event.key === "ArrowDown" ? 1 : -1)];
      next?.focus();
      next?.scrollIntoView({ block: "nearest" });
    }
  }

  return (
    <div className="table-wrap">
      <table className="data" aria-label="Audit log">
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.label}
                scope="col"
                className={column.className}
                aria-sort={column.key && column.key === params.sort ? (params.order === "asc" ? "ascending" : "descending") : undefined}
              >
                {column.key ? (
                  <button type="button" className="sort" onClick={() => onSort(column.key as SortKey)}>
                    {column.label}
                    <span className="arrow" aria-hidden="true">
                      {column.key === params.sort ? (params.order === "asc" ? "↑" : "↓") : ""}
                    </span>
                  </button>
                ) : (
                  column.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const open = openId === item.id;
            return (
              <Fragment key={item.id}>
                <tr
                  className={`row${item.success ? "" : " failed"}${open ? " open" : ""}`}
                  tabIndex={0}
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? null : item.id)}
                  onKeyDown={(e) => onRowKey(e, item.id)}
                >
                  <td title={formatFull(item.occurred_at)}>{formatTimestamp(item.occurred_at)}</td>
                  <td className={item.success ? "muted" : "status-failed"}>{item.success ? "ok" : "failed"}</td>
                  <td className="cell-clip" title={item.caller_sub}>
                    {item.caller_name ?? item.caller_sub}
                  </td>
                  <td>{item.tool_name}</td>
                  <td>{item.connection_name ?? <span className="muted">–</span>}</td>
                  <td className="cell-clip" title={item.tables.join(", ")}>
                    {item.tables.length ? item.tables.join(", ") : <span className="muted">–</span>}
                  </td>
                  <td className="cell-sql" title={item.sql_preview ?? undefined}>
                    {item.sql_preview ? (
                      <>
                        {item.sql_preview.replace(/\s+/g, " ")}
                        {item.sql_truncated && "…"}
                      </>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                  <td className="num">{item.row_count?.toLocaleString("en-GB") ?? <span className="muted">–</span>}</td>
                  <td className="num dur" style={{ ["--w" as string]: barWidth(item.duration_ms, slowest) }}>
                    {item.duration_ms.toLocaleString("en-GB")} ms
                  </td>
                </tr>
                {open && (
                  <tr className="detail">
                    <td colSpan={COLUMNS.length}>
                      <DetailPanel id={item.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DetailPanel({ id }: { id: number }) {
  const api = useApi();
  const detail = useAsync((signal) => api.get<AuditDetail>(`/audit/${id}`, undefined, signal), [api, id]);

  if (detail.error) return <ErrorNote onRetry={detail.reload}>{detail.error}</ErrorNote>;
  const d = detail.data;
  if (!d) return <div className="detail-body muted">Loading the full entry…</div>;

  const { sql, ...otherArguments } = d.arguments as Record<string, unknown> & { sql?: string };
  const extra = Object.fromEntries(Object.entries(otherArguments).filter(([key]) => key !== "connection_name"));

  return (
    <div className="detail-body">
      <dl>
        <dt>When</dt>
        <dd className="mono">{formatFull(d.occurred_at)}</dd>
        <dt>Caller</dt>
        <dd>
          {d.caller_name ?? "(no name)"} <span className="mono muted">{d.caller_sub}</span>
        </dd>
        {d.client_id && (
          <>
            <dt>Agent</dt>
            <dd className="mono">{d.client_id}</dd>
          </>
        )}
        <dt>Call</dt>
        <dd>
          {d.tool_name}
          {d.connection_name && (
            <>
              {" on "}
              <span className="mono">{d.connection_name}</span>
            </>
          )}
        </dd>
        {d.tables.length > 0 && (
          <>
            <dt>Tables</dt>
            <dd className="mono">{d.tables.join(", ")}</dd>
          </>
        )}
        <dt>Result</dt>
        <dd>
          {d.success ? "Succeeded" : <span className="status-failed">Failed</span>}
          {d.result_summary ? `: ${d.result_summary}` : ""}
          {" in "}
          {d.duration_ms.toLocaleString("en-GB")} ms
        </dd>
      </dl>
      {sql && (
        <div>
          <h3 className="muted">SQL</h3>
          <pre className="sql">{sql}</pre>
        </div>
      )}
      {Object.keys(extra).length > 0 && (
        <div>
          <h3 className="muted">Other arguments</h3>
          <pre className="sql">{JSON.stringify(extra, null, 2)}</pre>
        </div>
      )}
      {d.error_message && (
        <div>
          <h3 className="status-failed">Error</h3>
          <pre className="sql">{d.error_message}</pre>
        </div>
      )}
    </div>
  );
}
