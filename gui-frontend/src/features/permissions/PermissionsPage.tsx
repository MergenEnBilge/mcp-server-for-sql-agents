import { useSearchParams } from "react-router-dom";

import { useApi } from "../../api/client";
import type { Connection, TableGrid, ToolGrid } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { ErrorNote, PageHead, SegmentedControl } from "../../components/ui";
import { LoadFailure, TableAccess } from "./TableAccess";
import { ToolAccess } from "./ToolAccess";

/**
 * Who may use which database, see which tables, and call which tools. Changes are staged and
 * saved together; see useStagedGrid.
 */
export function PermissionsPage() {
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") === "tools" ? "tools" : "tables";

  function change(next: Record<string, string>) {
    const out = new URLSearchParams(search);
    for (const [key, value] of Object.entries(next)) {
      if (value) out.set(key, value);
      else out.delete(key);
    }
    setSearch(out, { replace: true });
  }

  return (
    <>
      <PageHead
        title="Permissions"
        sub="Access is off until someone grants it. Changes are staged here and take effect the moment you save."
        actions={
          <SegmentedControl
            label="What to manage"
            value={tab}
            options={[
              { value: "tables", label: "Tables" },
              { value: "tools", label: "Tools" },
            ]}
            onChange={(value) => change({ tab: value === "tools" ? "tools" : "" })}
          />
        }
      />
      {tab === "tables" ? <TablesTab selected={search.get("db") ?? ""} onSelect={(db) => change({ db })} /> : <ToolsTab />}
    </>
  );
}

function TablesTab({ selected, onSelect }: { selected: string; onSelect: (name: string) => void }) {
  const api = useApi();
  const connections = useAsync((signal) => api.get<Connection[]>("/connections", undefined, signal), [api]);
  const chosen = connections.data?.find((c) => c.name === selected) ?? connections.data?.[0];
  const grid = useAsync(
    (signal) => (chosen ? api.get<TableGrid>("/permissions/tables", { connection_id: chosen.id }, signal) : Promise.resolve(undefined)),
    [api, chosen?.id],
  );

  if (connections.error) return <ErrorNote onRetry={connections.reload}>{connections.error}</ErrorNote>;
  if (!connections.data) return <p className="muted" role="status">Loading databases…</p>;
  if (connections.data.length === 0) {
    return <div className="empty">No databases are registered yet, so there is nothing to grant. Register one under Connections.</div>;
  }

  return (
    <>
      <div className="filters">
        <label>
          <span className="muted">Database</span>
          <select className="select" aria-label="Database" value={chosen?.name ?? ""} onChange={(e) => onSelect(e.target.value)}>
            {connections.data.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name} ({c.engine})
              </option>
            ))}
          </select>
        </label>
      </div>
      {grid.error ? (
        <LoadFailure message={grid.error} onRetry={grid.reload} />
      ) : grid.data ? (
        <TableAccess key={grid.data.connection_id} grid={grid.data} onSaved={grid.reload} />
      ) : (
        <p className="muted" role="status">Reading the table list…</p>
      )}
    </>
  );
}

function ToolsTab() {
  const api = useApi();
  const grid = useAsync((signal) => api.get<ToolGrid>("/permissions/tools", undefined, signal), [api]);
  if (grid.error) return <ErrorNote onRetry={grid.reload}>{grid.error}</ErrorNote>;
  if (!grid.data) return <p className="muted" role="status">Loading…</p>;
  return <ToolAccess grid={grid.data} onSaved={grid.reload} />;
}
