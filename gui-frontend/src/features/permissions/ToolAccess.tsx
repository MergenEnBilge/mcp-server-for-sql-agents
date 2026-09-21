import { useCallback, useMemo, useState } from "react";

import { useApi } from "../../api/client";
import type { SubjectType, ToolGrid } from "../../api/types";
import { subjectLabel } from "../../components/format";
import { ConfirmDialog } from "../../components/ui";
import { AddSubject } from "./AddSubject";
import { GrantGrid, type GridColumn, type GridRow } from "./GrantGrid";
import { StagedBar } from "./StagedBar";
import { cellKey, groupByRow, splitCell, subjectKey, type Pending } from "./staging";
import { useStagedGrid } from "./useStagedGrid";

/** Which MCP tools a user or role may call at all, across every database. */
export function ToolAccess({ grid, onSaved }: { grid: ToolGrid; onSaved: () => void }) {
  const api = useApi();
  const [flipped, setFlipped] = useState(false);

  const granted = useMemo(() => new Set(grid.grants.map((g) => cellKey(subjectKey(g.subject_type, g.subject_id), g.tool))), [grid]);
  const labels = useMemo(() => new Map(grid.subjects.map((s) => [subjectKey(s.subject_type, s.subject_id), subjectLabel(s)])), [grid]);
  const saved = useCallback((row: string, col: string) => granted.has(cellKey(row, col)), [granted]);

  const write = useCallback(
    async (pending: Pending) => {
      for (const [row, list] of groupByRow(pending)) {
        const at = row.indexOf(":");
        await api.put("/permissions/tools", {
          subject_type: row.slice(0, at) as SubjectType,
          subject_id: row.slice(at + 1),
          changes: list.map((c) => ({ tool: c.col, granted: c.granted })),
        });
      }
    },
    [api],
  );

  const describeRevocation = useCallback(
    (row: string, cols: string[]) => `${labels.get(row) ?? row} will no longer be able to call ${cols.join(", ")}, on any database.`,
    [labels],
  );

  const staged = useStagedGrid({ saved, write, describeRevocation, onSaved });

  const rows: GridRow[] = grid.subjects.map((s) => ({ key: subjectKey(s.subject_type, s.subject_id), type: s.subject_type, label: subjectLabel(s) }));
  const columns: GridColumn[] = grid.tools.map((t) => ({ key: t.name, label: t.name, title: `${t.name}: ${t.description}`, mono: true }));

  const summary = useMemo(() => {
    const first = [...staged.pending][0];
    if (!first) return "";
    const [row, col] = splitCell(first[0]);
    const text = `${first[1] ? "Grant" : "Revoke"} ${col} ${first[1] ? "to" : "from"} ${labels.get(row) ?? row}`;
    return staged.pending.size > 1 ? `${text}, and ${staged.pending.size - 1} more.` : `${text}.`;
  }, [staged.pending, labels]);

  return (
    <>
      <div className="filters">
        <span className="muted">Every tool is read-only. This decides who may use each one at all.</span>
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
          caption="Tool access"
          cellLabel={(row, col) => `${row.label} can call ${col.label}`}
        />
      )}

      <StagedBar count={staged.pending.size} summary={summary} busy={staged.busy} saved={staged.savedMessage} error={staged.error} onSave={staged.requestSave} onDiscard={staged.discard} />

      <ConfirmDialog open={staged.confirming} title="Revoke tools?" confirmLabel="Revoke and save" destructive busy={staged.busy} onConfirm={staged.confirmSave} onCancel={staged.cancelConfirm}>
        <p>This takes effect immediately:</p>
        <ul>
          {staged.revokedLines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </ConfirmDialog>

      <AddSubject onAdded={onSaved} />
    </>
  );
}
