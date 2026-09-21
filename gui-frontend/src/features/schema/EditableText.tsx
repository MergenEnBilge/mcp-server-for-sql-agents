import { useEffect, useRef, useState } from "react";

import type { DescriptionSaved } from "../../api/types";

export const MAX_DESCRIPTION = 2000;

/**
 * A description that is edited where it is read. Click (or press Enter) to edit; Enter saves,
 * Shift+Enter adds a line, Escape gives up. While the text differs from what is saved it says
 * "Unsaved", and after saving it says "Saved", so it is never unclear what is in the database.
 */
export function EditableText({
  value,
  label,
  placeholder = "Add a description",
  withheldRule,
  onSave,
}: {
  value: string;
  /** What this text describes, for screen readers, e.g. "Description of orders.total". */
  label: string;
  placeholder?: string;
  withheldRule: string | null;
  onSave: (text: string) => Promise<DescriptionSaved>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justSaved, setJustSaved] = useState(false);
  const [warning, setWarning] = useState<string | null>(withheldRule);
  const area = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement>(null);

  useEffect(() => setWarning(withheldRule), [withheldRule]);
  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);
  useEffect(() => {
    if (editing) area.current?.focus();
  }, [editing]);

  const dirty = draft.trim() !== value.trim();

  function stop() {
    setEditing(false);
    setError(null);
    setDraft(value);
    opener.current?.focus();
  }

  async function save() {
    if (!dirty) return stop();
    setBusy(true);
    setError(null);
    try {
      const saved = await onSave(draft);
      setWarning(saved.withheld_rule);
      setJustSaved(true);
      window.setTimeout(() => setJustSaved(false), 4000);
      setEditing(false);
      setTimeout(() => opener.current?.focus(), 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Saving failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="editable">
      {editing ? (
        <>
          <textarea
            ref={area}
            className="input"
            aria-label={label}
            value={draft}
            rows={Math.min(8, Math.max(2, draft.split("\n").length + 1))}
            maxLength={MAX_DESCRIPTION}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void save();
              } else if (e.key === "Escape") {
                e.preventDefault();
                stop();
              }
            }}
          />
          <div className="editable-bar">
            <span className={dirty ? "unsaved" : "muted"} role="status">
              {busy ? "Saving…" : dirty ? "Unsaved" : "No changes"}
            </span>
            <span className="muted">Enter saves · Shift+Enter for a new line · Esc cancels</span>
            <span className="grow" />
            <button type="button" className="btn" onClick={stop} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={busy || !dirty}>
              Save
            </button>
          </div>
        </>
      ) : (
        <button ref={opener} type="button" className="editable-view" aria-label={`${label}: ${value || "empty"}. Edit`} onClick={() => setEditing(true)}>
          {value ? <span>{value}</span> : <span className="muted">{placeholder}</span>}
          {justSaved && <span className="saved-mark">Saved</span>}
        </button>
      )}
      {error && (
        <div className="note fault" role="alert">
          Not saved. {error}
        </div>
      )}
      {warning && (
        <div className="note fault" role="alert">
          The MCP server will not pass this on. It reads like an instruction to an AI ({warning.replace(/_/g, " ")}), so it is withheld and the call is flagged.
          Reword it as a plain description of the data.
        </div>
      )}
    </div>
  );
}
