import { useCallback, useMemo, useState } from "react";

import { useApi } from "../../api/client";
import type { SubjectType, TableGrid } from "../../api/types";
import { subjectLabel } from "../../components/format";
import { ConfirmDialog, ErrorNote } from "../../components/ui";
import { AddSubject } from "./AddSubject";
import { GrantGrid, type GridColumn, type GridRow } from "./GrantGrid";
import { StagedBar } from "./StagedBar";
import { cellKey, groupByRow, splitCell, subjectKey, type Pending } from "./staging";
import { useStagedGrid } from "./useStagedGrid";

/** The leading column: may this subject use the database at all? Table grants only matter with it. */
export const USE = "__use__";

const parseRow = (key: string): [SubjectType, string] => {
  const at = key.indexOf(":");
  return [key.slice(0, at) as SubjectType, key.slice(at + 1)];
};

export function TableAccess({ grid, onSaved }: { grid: TableGrid; onSaved: () => void }) {
  const api = useApi();
  const [filter, setFilter] = useState("");
  const [flipped, setFlipped] = useState(false);

  const usable = useMemo(() => new Set(grid.with_connection_access.map(([t, id]) => subjectKey(t, id))), [grid]);
  const granted = useMemo(() => new Set(grid.grants.map((g) => cellKey(subjectKey(g.subject_type, g.subject_id), g.table))), [grid]);
  const labels = useMemo(() => new Map(grid.subjects.map((s) => [subjectKey(s.subject_type, s.subject_id), subjectLabel(s)])), [grid]);

  const saved = useCallback((row: string, col: string) => (col === USE ? usable.has(row) : granted.has(cellKey(row, col))), [usable, granted]);

  const write = useCallback(
    async (pending: Pending) => {
      const byRow = groupByRow(pending);
      // Access to the database first, so the table grants that follow apply to someone who can use it.
      const useChanges = [...byRow].flatMap(([row, list]) => list.filter((c) => c.col === USE).map((c) => ({ row, granted: c.granted })));
      if (useChanges.length > 0) {
        const next = new Set(usable);
        for (const c of useChanges) {
          if (c.granted) next.add(c.row);
          else next.delete(c.row);
        }
        await api.put(`/connections/${grid.connection_id}/access`, {
          subjects: [...next].map((key) => {
            const [subject_type, subject_id] = parseRow(key);
            return { subject_type, subject_id };
          }),
        });
      }
      for (const [row, list] of byRow) {
        const tables = list.filter((c) => c.col !== USE).map((c) => ({ table: c.col, granted: c.granted }));
        if (tables.length === 0) continue;
        const [subject_type, subject_id] = parseRow(row);
        await api.put("/permissions/tables", { connection_id: grid.connection_id, subject_type, subject_id, changes: tables });
      }
    },
    [api, grid.connection_id, usable],
  );

  const describeRevocation = useCallback(
    (row: string, cols: string[]) => {
      const who = labels.get(row) ?? row;
      const parts: string[] = [];
      if (cols.includes(USE)) parts.push(`${who} will no longer be able to use ${grid.connection_name} at all.`);
      const tables = cols.filter((c) => c !== USE);
      if (tables.length) parts.push(`${who} will lose access to ${tables.join(", ")}. Any query touching ${tables.length === 1 ? "it" : "them"} is refused from their next request.`);
      return parts.join(" ");
    },
    [labels, grid.connection_name],
  );

  const staged = useStagedGrid({ saved, write, describeRevocation, onSaved });

  const rows: GridRow[] = grid.subjects.map((s) => {
    const key = subjectKey(s.subject_type, s.subject_id);
    return { key, type: s.subject_type, label: subjectLabel(s), dimmed: (col) => col !== USE && !staged.isOn(key, USE) };
  });

  const needle = filter.trim().toLowerCase();
  const columns: GridColumn[] = [
    { key: USE, label: "Can use", title: `May use ${grid.connection_name} at all`, divider: true },
    ...grid.tables
      .filter((t) => !needle || t.name.toLowerCase().includes(needle))
      .map((t) => ({ key: t.name, label: t.name, title: t.description ? `${t.name}: ${t.description}` : t.name, mono: true })),
  ];

  const summary = useMemo(() => {
    const first = [...staged.pending][0];
    if (!first) return "";
    const [row, col] = splitCell(first[0]);
    const what = col === USE ? `use of ${grid.connection_name}` : col;
    const text = `${first[1] ? "Grant" : "Revoke"} ${what} ${first[1] ? "to" : "from"} ${labels.get(row) ?? row}`;
    return staged.pending.size > 1 ? `${text}, and ${staged.pending.size - 1} more.` : `${text}.`;
  }, [staged.pending, labels, grid.connection_name]);

  return (
    <>
      {!grid.reachable && (
        <div className="note fault" role="alert" style={{ marginBottom: 12 }}>
          <strong>Couldn&rsquo;t read this database&rsquo;s tables.</strong> {grid.error} Showing only the tables that already have grants,
          so access can still be reviewed and revoked.
        </div>
      )}

      <div className="filters">
        <label>
          <span className="visually-hidden">Filter tables</span>
          <input className="input" type="search" placeholder="Filter tables" aria-label="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        <span className="muted">
          {columns.length - 1} of {grid.tables.length} tables shown
        </span>
        <button type="button" className="btn" onClick={() => setFlipped((f) => !f)} aria-pressed={flipped}>
          Flip axes
        </button>
      </div>

      {grid.subjects.length === 0 ? (
        <div className="empty">No users or roles are known yet. Add a role below, or wait until someone signs in.</div>
      ) : (
        <GrantGrid
          rows={rows}
          columns={columns}
          saved={saved}
          pending={staged.pending}
          onSet={staged.set}
          flipped={flipped}
          caption={`Table access on ${grid.connection_name}`}
          cellLabel={(row, col) => (col.key === USE ? `${row.label} can use ${grid.connection_name}` : `${row.label} can read ${col.label}`)}
        />
      )}

      <StagedBar
        count={staged.pending.size}
        summary={summary}
        busy={staged.busy}
        saved={staged.savedMessage}
        error={staged.error}
        onSave={staged.requestSave}
        onDiscard={staged.discard}
      />

      <ConfirmDialog
        open={staged.confirming}
        title="Revoke access?"
        confirmLabel="Revoke and save"
        destructive
        busy={staged.busy}
        onConfirm={staged.confirmSave}
        onCancel={staged.cancelConfirm}
      >
        <p>This takes effect immediately:</p>
        <ul>
          {staged.revokedLines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {staged.pending.size > staged.revokedLines.length && <p className="muted">Other staged changes are saved at the same time.</p>}
      </ConfirmDialog>

      <AddSubject onAdded={onSaved} />
    </>
  );
}

export function LoadFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <ErrorNote onRetry={onRetry}>{message}</ErrorNote>;
}
