import { useState } from "react";

import { useApi } from "../../api/client";
import type { Connection, Report } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatTimestamp } from "../../components/format";
import { ConfirmDialog, ErrorNote, PageHead } from "../../components/ui";
import { ReportForm } from "./ReportForm";

/**
 * Saved reports: a plain list for now. The natural-language BI app that will create these
 * doesn't exist yet, so this screen is deliberately small.
 */
export function ReportsPage() {
  const api = useApi();
  const [search, setSearch] = useState("");
  const reports = useAsync((signal) => api.get<Report[]>("/reports", { q: search.trim() }, signal), [api, search]);
  const connections = useAsync((signal) => api.get<Connection[]>("/connections", undefined, signal), [api]);

  const [panel, setPanel] = useState<"new" | string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const selected = reports.data?.find((r) => r.id === panel) ?? null;

  async function remove() {
    if (!selected) return;
    try {
      await api.del(`/reports/${selected.id}`);
      setMessage(`Deleted ${selected.name}.`);
      setPanel(null);
      reports.reload();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Deleting failed.");
    } finally {
      setConfirmDelete(false);
    }
  }

  return (
    <>
      <PageHead
        title="Saved reports"
        sub="Named queries with a chart setting, kept for the reporting app that will use them."
        actions={
          <button
            type="button"
            className="btn btn-primary"
            disabled={!connections.data?.length}
            onClick={() => {
              setPanel("new");
              setMessage(null);
            }}
          >
            Add report
          </button>
        }
      />
      {message && (
        <div className="note" role="status" style={{ marginBottom: 12 }}>
          {message}
        </div>
      )}
      {deleteError && <ErrorNote>{deleteError}</ErrorNote>}
      <div className="filters">
        <input className="input" type="search" aria-label="Search reports" placeholder="Search by name or description" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {reports.error ? (
        <ErrorNote onRetry={reports.reload}>{reports.error}</ErrorNote>
      ) : !reports.data || !connections.data ? (
        <p className="muted" role="status">
          Loading reports…
        </p>
      ) : (
        <div className={`split${panel ? " with-panel" : ""}`}>
          <div>
            {reports.data.length === 0 ? (
              <div className="empty">
                {search ? "No reports match that search." : connections.data.length === 0 ? "Register a database first; every report belongs to one." : "No reports have been saved yet."}
              </div>
            ) : (
              <div className="table-wrap">
                <table className="data" aria-label="Saved reports">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Database</th>
                      <th scope="col">Chart</th>
                      <th scope="col">Owner</th>
                      <th scope="col">Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.data.map((r) => (
                      <tr key={r.id} className={`row${r.id === panel ? " open" : ""}`}>
                        <td>
                          <button
                            type="button"
                            className="link"
                            onClick={() => {
                              setPanel(r.id);
                              setMessage(null);
                            }}
                          >
                            {r.name}
                          </button>
                          {r.description && <div className="muted clip">{r.description}</div>}
                        </td>
                        <td>{r.connection_name}</td>
                        <td>{(r.chart_config.type as string | undefined) ?? <span className="muted">table</span>}</td>
                        <td title={r.owner_sub}>{r.owner_name ?? r.owner_sub}</td>
                        <td>{formatTimestamp(r.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {(panel === "new" || selected) && (
            <ReportForm
              key={panel}
              existing={selected}
              connections={connections.data}
              onSaved={(saved) => {
                setMessage(`Saved ${saved.name}.`);
                setPanel(saved.id);
                reports.reload();
              }}
              onClose={() => setPanel(null)}
              onDelete={() => setConfirmDelete(true)}
            />
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${selected?.name ?? "report"}?`}
        confirmLabel="Delete report"
        destructive
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(false)}
      >
        <p>
          The saved query and chart setting for <strong>{selected?.name}</strong> are removed. The database it queried is not touched. The deletion is recorded in the admin log.
        </p>
      </ConfirmDialog>
    </>
  );
}
