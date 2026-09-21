import { useMemo } from "react";

import type { SubjectType } from "../../api/types";
import { cellKey, effective, type Pending } from "./staging";

export interface GridRow {
  key: string;
  type: SubjectType;
  label: string;
  /** Cells in these columns are dimmed (e.g. no access to the database at all). */
  dimmed?: (colKey: string) => boolean;
}

export interface GridColumn {
  key: string;
  label: string;
  title?: string;
  /** A heavier rule after this column, to set apart the "can use" column. */
  divider?: boolean;
  mono?: boolean;
}

interface Props {
  rows: GridRow[];
  columns: GridColumn[];
  saved: (rowKey: string, colKey: string) => boolean;
  pending: Pending;
  /** Ask for these cells to have this value. Several at once for the "all" and "none" buttons. */
  onSet: (cells: { row: string; col: string }[], granted: boolean) => void;
  flipped: boolean;
  cellLabel: (row: GridRow, col: GridColumn) => string;
  caption: string;
}

/**
 * The matrix itself: real checkboxes (so it works with a keyboard and screen readers) styled as
 * squares. Rows and columns can be swapped, because whichever axis is longer reads better down the
 * page. Which axis is which is only a matter of drawing; changes always name (row, column) the same way.
 */
export function GrantGrid({ rows, columns, saved, pending, onSet, flipped, cellLabel, caption }: Props) {
  const rowByKey = useMemo(() => new Map(rows.map((r) => [r.key, r])), [rows]);
  const colByKey = useMemo(() => new Map(columns.map((c) => [c.key, c])), [columns]);

  const down = flipped ? columns : rows; // what runs down the page
  const across = flipped ? rows : columns; // what runs across it

  const isOn = (rowKey: string, colKey: string) => effective(pending, cellKey(rowKey, colKey), saved(rowKey, colKey));
  const pair = (a: { key: string }, b: { key: string }) => (flipped ? { row: b.key, col: a.key } : { row: a.key, col: b.key });

  // Everything along one line of the drawn grid, as (row, column) cells.
  const lineCells = (line: { key: string }, other: readonly { key: string }[]) => other.map((o) => pair(line, o));
  const allOn = (cells: { row: string; col: string }[]) => cells.every((c) => isOn(c.row, c.col));

  function bulk(line: { key: string }, other: readonly { key: string }[], label: string) {
    const cells = lineCells(line, other);
    const target = !allOn(cells);
    return (
      <button type="button" className="bulk" aria-label={`${target ? "Grant all" : "Revoke all"} for ${label}`} onClick={() => onSet(cells, target)}>
        {target ? "all" : "none"}
      </button>
    );
  }

  const labelOf = (item: GridRow | GridColumn) => item.label;

  return (
    <div className="grid-wrap">
      <table className="grid" aria-label={caption}>
        <thead>
          <tr>
            <th className="stub" scope="col">
              {flipped ? "Table or tool" : "User or role"}
            </th>
            {across.map((a) => {
              const asColumn = colByKey.get(a.key);
              return (
                <th key={a.key} scope="col" className={asColumn?.divider ? "divider" : undefined} title={asColumn?.title ?? labelOf(a as GridRow)}>
                  <span className={asColumn?.mono ? "mono" : undefined}>{labelOf(a as GridRow)}</span>
                  {bulk(a, down, labelOf(a as GridRow))}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {down.map((d) => {
            const asRow = rowByKey.get(d.key);
            const asColumn = colByKey.get(d.key);
            return (
              <tr key={d.key}>
                <th className="stub who" scope="row">
                  {asRow && <span className="chip">{asRow.type}</span>}
                  <span className={asColumn?.mono ? "mono" : undefined}>{labelOf(d as GridRow)}</span>
                  {bulk(d, across, labelOf(d as GridRow))}
                </th>
                {across.map((a) => {
                  const { row, col } = pair(d, a);
                  const rowItem = rowByKey.get(row) as GridRow;
                  const colItem = colByKey.get(col) as GridColumn;
                  const key = cellKey(row, col);
                  const on = isOn(row, col);
                  const dimmed = rowItem.dimmed?.(col) && !on;
                  return (
                    <td key={a.key} className={`${colItem.divider ? "divider" : ""}${dimmed ? " unusable" : ""}`}>
                      <input
                        type="checkbox"
                        className="grant"
                        checked={on}
                        data-pending={pending.has(key) ? "true" : undefined}
                        aria-label={cellLabel(rowItem, colItem)}
                        onChange={(e) => onSet([{ row, col }], e.target.checked)}
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
