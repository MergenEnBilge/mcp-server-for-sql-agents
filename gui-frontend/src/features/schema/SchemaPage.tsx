import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useApi } from "../../api/client";
import type { Connection, SchemaTableList } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { ConfirmDialog, ErrorNote, PageHead } from "../../components/ui";
import { TableEditor } from "./TableEditor";

/**
 * The descriptions the AI reads. Browse the tables of one database and edit in place; the
 * structure itself comes from the database and isn't editable here.
 */
export function SchemaPage() {
  const api = useApi();
  const [search, setSearch] = useSearchParams();
  const [filter, setFilter] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const connections = useAsync((signal) => api.get<Connection[]>("/connections", undefined, signal), [api]);
  const chosen = connections.data?.find((c) => c.name === search.get("db")) ?? connections.data?.[0];
  const list = useAsync(
    (signal) => (chosen ? api.get<SchemaTableList>("/schema/tables", { connection_id: chosen.id }, signal) : Promise.resolve(undefined)),
    [api, chosen?.id],
  );

  function pick(next: Record<string, string>) {
    const out = new URLSearchParams(search);
    for (const [key, value] of Object.entries(next)) {
      if (value) out.set(key, value);
      else out.delete(key);
    }
    setSearch(out, { replace: true });
  }

  async function removeStale() {
    if (!chosen || !removing) return;
    try {
      await api.del(`/schema/description?connection_id=${encodeURIComponent(chosen.id)}&table=${encodeURIComponent(removing)}`);
      setRemoving(null);
      list.reload();
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : "Removing failed.");
      setRemoving(null);
    }
  }

  if (connections.error) return <ErrorNote onRetry={connections.reload}>{connections.error}</ErrorNote>;
  if (!connections.data) return <p className="muted" role="status">Loading…</p>;

  const selectedTable = search.get("table") ?? "";
  const needle = filter.trim().toLowerCase();
  const tables = (list.data?.tables ?? []).filter((t) => !needle || t.name.toLowerCase().includes(needle) || t.description.toLowerCase().includes(needle));

  return (
    <>
      <PageHead title="Schema descriptions" sub="What the AI is told about each table and column. Plain, factual sentences work best: what it holds, what the units are, what the odd values mean." />
      {connections.data.length === 0 ? (
        <div className="empty">No databases are registered yet. Register one under Connections first.</div>
      ) : (
        <>
          <div className="filters">
            <label>
              <span className="muted">Database</span>
              <select className="select" aria-label="Database" value={chosen?.name ?? ""} onChange={(e) => pick({ db: e.target.value, table: "" })}>
                {connections.data.map((c) => (
                  <option key={c.id} value={c.name}>
                    {c.name} ({c.engine})
                  </option>
                ))}
              </select>
            </label>
          </div>
          {removeError && <ErrorNote>{removeError}</ErrorNote>}
          {list.error ? (
            <ErrorNote onRetry={list.reload}>{list.error}</ErrorNote>
          ) : !list.data || !chosen ? (
            <p className="muted" role="status">Reading the table list…</p>
          ) : (
            <div className="editor-split">
              <nav aria-label="Tables" className="table-list">
                {!list.data.reachable && (
                  <div className="note fault" role="alert">
                    Couldn&rsquo;t read this database: {list.data.error} Showing only the tables that already have descriptions.
                  </div>
                )}
                <input className="input" type="search" aria-label="Filter tables" placeholder="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} />
                {tables.length === 0 ? (
                  <p className="muted" style={{ padding: "10px 2px" }}>
                    {list.data.tables.length === 0 ? "This database has no tables." : "No tables match that filter."}
                  </p>
                ) : (
                  <ul>
                    {tables.map((t) => (
                      <li key={t.name}>
                        <button type="button" aria-current={t.name === selectedTable ? "true" : undefined} onClick={() => pick({ table: t.name })}>
                          <span className="mono name">{t.name}</span>
                          <span className="meta">
                            {t.missing ? (
                              <span className="status-failed">no longer in the database</span>
                            ) : t.description ? (
                              `${t.described_columns} column${t.described_columns === 1 ? "" : "s"} described`
                            ) : (
                              <span className="muted">not described</span>
                            )}
                          </span>
                        </button>
                        {t.missing && (
                          <button type="button" className="btn btn-quiet btn-danger" onClick={() => setRemoving(t.name)}>
                            Remove
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </nav>
              <div className="editor-pane">
                {selectedTable && list.data.tables.some((t) => t.name === selectedTable && !t.missing) ? (
                  <TableEditor key={`${chosen.id}/${selectedTable}`} connectionId={chosen.id} connectionName={chosen.name} table={selectedTable} onChanged={list.reload} />
                ) : (
                  <div className="empty">Pick a table to read and edit what the AI is told about it.</div>
                )}
              </div>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={removing !== null}
        title="Remove these descriptions?"
        confirmLabel="Remove"
        destructive
        onConfirm={() => void removeStale()}
        onCancel={() => setRemoving(null)}
      >
        <p>
          <span className="mono">{removing}</span> is no longer in {chosen?.name}. Its table and column descriptions will be deleted. The database is not touched.
        </p>
      </ConfirmDialog>
    </>
  );
}
