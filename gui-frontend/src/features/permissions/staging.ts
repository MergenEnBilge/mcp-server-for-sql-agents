/**
 * Staged (not yet saved) permission changes.
 *
 * Toggling a cell never writes anything. It records what the person *wants* in a map keyed by
 * (subject, thing). A cell whose wanted value equals what is saved isn't a change at all, so
 * toggling something twice cleanly undoes it. Nothing reaches the server until Save.
 */

export const SEP = "\u0000";

/** Identifies a row: a user or a role. */
export const subjectKey = (type: string, id: string) => `${type}:${id}`;

export const cellKey = (row: string, col: string) => `${row}${SEP}${col}`;

export function splitCell(key: string): [row: string, col: string] {
  const at = key.indexOf(SEP);
  return [key.slice(0, at), key.slice(at + 1)];
}

/** Wanted values that differ from what is saved. */
export type Pending = ReadonlyMap<string, boolean>;

/** Ask for a cell to have `wanted`, given what is saved. Returns a new map. */
export function stage(pending: Pending, key: string, saved: boolean, wanted: boolean): Pending {
  const next = new Map(pending);
  if (wanted === saved) next.delete(key);
  else next.set(key, wanted);
  return next;
}

export function effective(pending: Pending, key: string, saved: boolean): boolean {
  return pending.has(key) ? (pending.get(key) as boolean) : saved;
}

export interface Change {
  row: string;
  col: string;
  granted: boolean;
}

export function changes(pending: Pending): Change[] {
  return [...pending].map(([key, granted]) => {
    const [row, col] = splitCell(key);
    return { row, col, granted };
  });
}

/** Changes grouped by subject, which is how the API takes them (one call per subject). */
export function groupByRow(pending: Pending): Map<string, { col: string; granted: boolean }[]> {
  const grouped = new Map<string, { col: string; granted: boolean }[]>();
  for (const { row, col, granted } of changes(pending)) {
    const list = grouped.get(row) ?? [];
    list.push({ col, granted });
    grouped.set(row, list);
  }
  return grouped;
}

/** What will be revoked, per subject: the part that needs an explicit confirmation. */
export function revocations(pending: Pending): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const { row, col, granted } of changes(pending)) {
    if (granted) continue;
    out.set(row, [...(out.get(row) ?? []), col]);
  }
  return out;
}
