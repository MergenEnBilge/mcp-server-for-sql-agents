import { useState, type FormEvent } from "react";

import { useApi } from "../../api/client";
import type { SubjectType } from "../../api/types";

/** Add a role (or a user) by name, e.g. a role that nobody holds yet, so it can be granted things. */
export function AddSubject({ onAdded }: { onAdded: () => void }) {
  const api = useApi();
  const [type, setType] = useState<SubjectType>("role");
  const [id, setId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; fault: boolean } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const name = id.trim();
    if (!name) return;
    setBusy(true);
    try {
      await api.post("/permissions/subjects", { subject_type: type, subject_id: name });
      setMessage({ text: `Added ${type} ${name}. It now appears in the grid.`, fault: false });
      setId("");
      onAdded();
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : "Could not add that.", fault: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="filters" style={{ marginTop: 20 }} onSubmit={submit} aria-label="Add a user or role">
      <strong>Add to the grid</strong>
      <label>
        <span className="visually-hidden">Kind</span>
        <select className="select" aria-label="Kind" value={type} onChange={(e) => setType(e.target.value as SubjectType)}>
          <option value="role">Role</option>
          <option value="user">User id</option>
        </select>
      </label>
      <input className="input" aria-label="Name" placeholder={type === "role" ? "Role name, e.g. finance" : "User id from the identity provider"} value={id} onChange={(e) => setId(e.target.value)} />
      <button type="submit" className="btn" disabled={busy || !id.trim()}>
        Add
      </button>
      {message && (
        <span className={message.fault ? "status-failed" : "muted"} role="status">
          {message.text}
        </span>
      )}
    </form>
  );
}
