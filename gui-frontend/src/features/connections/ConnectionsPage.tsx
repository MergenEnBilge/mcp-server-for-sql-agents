import { useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../../api/client";
import type { Connection, ConnectionTestResult, EngineInfo } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatTimestamp, plural } from "../../components/format";
import { ConfirmDialog, ErrorNote, PageHead } from "../../components/ui";
import { ConnectionForm } from "./ConnectionForm";

/** "Reachable", "Failed" or "Not checked yet": the state of the last test, and when it was run. */
function Status({ c }: { c: Connection }) {
  if (c.last_check_ok === null) {
    return (
      <>
        <span className="dot" aria-hidden="true" />
        Not checked yet
      </>
    );
  }
  return c.last_check_ok ? (
    <>
      <span className="dot live" aria-hidden="true" />
      Reachable
    </>
  ) : (
    <>
      <span className="dot fault" aria-hidden="true" />
      <span className="status-failed">Failed</span>
    </>
  );
}

export function ConnectionsPage() {
  const api = useApi();
  const connections = useAsync((signal) => api.get<Connection[]>("/connections", undefined, signal), [api]);
  const engines = useAsync((signal) => api.get<EngineInfo[]>("/engines", undefined, signal), [api]);

  const [panel, setPanel] = useState<"new" | string | null>(null); // "new", a connection id, or closed
  const [message, setMessage] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const selected = connections.data?.find((c) => c.id === panel) ?? null;

  async function test(c: Connection) {
    setTesting(c.id);
    setMessage(null);
    try {
      const result = await api.post<ConnectionTestResult>(`/connections/${c.id}/test`);
      setMessage(`${c.name}: ${result.message}`);
    } catch (e) {
      setMessage(`${c.name}: ${e instanceof Error ? e.message : "The test could not be run."}`);
    } finally {
      setTesting(null);
      connections.reload();
    }
  }

  async function remove() {
    if (!selected) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.del(`/connections/${selected.id}`);
      setMessage(`Deleted ${selected.name}. The MCP server no longer offers it.`);
      setPanel(null);
      setConfirmDelete(false);
      connections.reload();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Deleting failed.");
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <PageHead
        title="Connections"
        sub="The databases the MCP server can query. Passwords are stored encrypted and are never shown again."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => { setPanel("new"); setMessage(null); }}>
            Add connection
          </button>
        }
      />
      {message && (
        <div className="note" role="status" style={{ marginBottom: 12 }}>
          {message}
        </div>
      )}
      {deleteError && <ErrorNote>{deleteError}</ErrorNote>}

      {connections.error ? (
        <ErrorNote onRetry={connections.reload}>{connections.error}</ErrorNote>
      ) : !connections.data || !engines.data ? (
        <p className="muted" role="status">
          Loading connections…
        </p>
      ) : (
        <div className={`split${panel ? " with-panel" : ""}`}>
          <div>
            {connections.data.length === 0 ? (
              <div className="empty">No databases are registered yet. Add one to let the MCP server query it.</div>
            ) : (
              <div className="table-wrap">
                <table className="data" aria-label="Connections">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Engine</th>
                      <th scope="col">Status</th>
                      <th scope="col">Last checked</th>
                      <th scope="col">Who may use it</th>
                      <th scope="col">
                        <span className="visually-hidden">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {connections.data.map((c) => (
                      <tr key={c.id} className={`row${c.last_check_ok === false ? " failed" : ""}${c.id === panel ? " open" : ""}`}>
                        <td>
                          <button type="button" className="link" onClick={() => { setPanel(c.id); setMessage(null); }}>
                            {c.name}
                          </button>
                          {!c.is_active && <span className="chip" style={{ marginLeft: 6 }}>off</span>}
                        </td>
                        <td>{engines.data?.find((e) => e.engine === c.engine)?.label ?? c.engine}</td>
                        <td title={c.last_check_error ?? undefined}>
                          <Status c={c} />
                          {c.last_check_ok === false && c.last_check_error && <div className="muted clip">{c.last_check_error}</div>}
                        </td>
                        <td>{c.last_checked_at ? formatTimestamp(c.last_checked_at) : <span className="muted">–</span>}</td>
                        <td>
                          <Link to={`/permissions?db=${encodeURIComponent(c.name)}`}>{c.access_count === 0 ? "nobody yet" : plural(c.access_count, "user or role", "users and roles")}</Link>
                        </td>
                        <td>
                          <button type="button" className="btn" disabled={testing !== null} onClick={() => void test(c)} aria-label={`Test ${c.name}`}>
                            {testing === c.id ? "Testing…" : "Test"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {(panel === "new" || selected) && (
            <ConnectionForm
              key={panel}
              existing={selected}
              engines={engines.data}
              onSaved={(saved) => {
                setMessage(`Saved ${saved.name}. The MCP server uses the new settings from its next request.`);
                setPanel(saved.id);
                connections.reload();
              }}
              onClose={() => setPanel(null)}
              onDelete={() => setConfirmDelete(true)}
            />
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${selected?.name ?? "connection"}?`}
        confirmLabel="Delete connection"
        destructive
        busy={deleting}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(false)}
      >
        <p>This takes effect immediately:</p>
        <ul>
          <li>The MCP server stops offering {selected?.name} to anyone.</li>
          <li>{selected ? plural(selected.access_count, "access grant") : "Its access grants"}, and every table permission and schema description for it, are deleted.</li>
          <li>Past audit entries stay, so history is kept.</li>
        </ul>
        <p className="muted">The database itself is not touched. A connection that saved reports still use can&rsquo;t be deleted.</p>
      </ConfirmDialog>
    </>
  );
}
