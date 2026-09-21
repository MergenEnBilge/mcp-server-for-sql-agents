import { useState, type FormEvent } from "react";

import { useApi } from "../../api/client";
import type { Connection, ConnectionTestResult, EngineInfo } from "../../api/types";
import { formatOptions, parseConnectionString, parseOptions } from "./connectionString";

const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,62}$/;

/** What people most often need to set as a driver option, per engine. */
const OPTION_HINTS: Record<string, string> = {
  postgresql: "Optional, one name=value per line. To require an encrypted connection: ssl=require",
  mysql: "Optional, one name=value per line. For an encrypted connection: ssl_ca=/path/to/ca.pem",
  mssql: "Optional, one name=value per line. Default driver: ODBC Driver 18 for SQL Server. Often needed: TrustServerCertificate=yes",
};

const text = (value: unknown) => (value === undefined || value === null ? "" : String(value));

/** Turn what was typed into the (non-secret) settings the API stores. */
export function buildDetails(
  engine: string,
  f: { host: string; port: string; database: string; username: string; path: string; schemas: string; options?: string },
) {
  if (engine === "sqlite") return { path: f.path.trim() };
  const schemas = f.schemas
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const options = parseOptions(f.options ?? "");
  return {
    ...(Object.keys(options).length ? { options } : {}),
    host: f.host.trim(),
    ...(f.port.trim() ? { port: Number(f.port) } : {}),
    database: f.database.trim(),
    username: f.username.trim(),
    ...(schemas.length ? { schemas } : {}),
  };
}

/**
 * Add or edit one connection. The credential field is write-only: for a saved connection it says
 * "Stored. Not shown." and only accepts a replacement, so nothing here can leak a password.
 */
export function ConnectionForm({
  existing,
  engines,
  onSaved,
  onClose,
  onDelete,
}: {
  existing: Connection | null;
  engines: EngineInfo[];
  onSaved: (saved: Connection) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  const api = useApi();
  const editing = existing !== null;
  const d = existing?.details ?? {};

  const [name, setName] = useState(existing?.name ?? "");
  const [engine, setEngine] = useState(existing?.engine ?? engines[0]?.engine ?? "postgresql");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [host, setHost] = useState(text(d.host));
  const [port, setPort] = useState(text(d.port));
  const [database, setDatabase] = useState(text(d.database));
  const [username, setUsername] = useState(text(d.username));
  const [path, setPath] = useState(text(d.path));
  const [schemas, setSchemas] = useState(Array.isArray(d.schemas) ? d.schemas.join(", ") : "");
  const [options, setOptions] = useState(formatOptions(d.options));
  const [pasted, setPasted] = useState("");
  const [pasteNote, setPasteNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [active, setActive] = useState(existing?.is_active ?? true);

  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<ConnectionTestResult | null>(null);

  const info = engines.find((e) => e.engine === engine);
  const isFile = engine === "sqlite";
  const details = buildDetails(engine, { host, port, database, username, path, schemas, options });
  const unavailable = info && !info.available ? info : null;

  function fillFromConnectionString() {
    const parsed = parseConnectionString(pasted);
    if ("error" in parsed) {
      setPasteNote({ ok: false, text: parsed.error });
      return;
    }
    setEngine(parsed.engine);
    setHost(parsed.host);
    setPort(parsed.port);
    setDatabase(parsed.database);
    setUsername(parsed.username);
    setPath(parsed.path);
    setOptions(parsed.options);
    setSecret(parsed.password);
    setClearSecret(false);
    setPasted(""); // the string may hold a password: don't leave it on screen
    setPasteNote({
      ok: true,
      text: parsed.password ? "Filled in below. The password went into the password field." : "Filled in below.",
    });
  }
  const nameProblem = !editing && name !== "" && !NAME_PATTERN.test(name);
  const complete = (editing || NAME_PATTERN.test(name)) && (info?.required ?? []).every((f) => text((details as Record<string, unknown>)[f]) !== "");

  async function runTest() {
    setBusy("test");
    setTest(null);
    setError(null);
    try {
      setTest(await api.post<ConnectionTestResult>("/connections/test", { engine, details, secret: secret || null, connection_id: existing?.id ?? null }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The test could not be run.");
    } finally {
      setBusy(null);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy("save");
    setError(null);
    try {
      const saved = editing
        ? await api.put<Connection>(`/connections/${existing.id}`, { description, details, secret: secret || null, clear_secret: clearSecret, is_active: active })
        : await api.post<Connection>("/connections", { name, engine, description, details, secret: secret || null, is_active: active });
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Saving failed.");
    } finally {
      setBusy(null);
    }
  }

  const field = (label: string, control: React.ReactNode, hint?: string) => (
    <label className="field">
      <span>{label}</span>
      {control}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );

  return (
    <form className="panel" onSubmit={save} aria-label={editing ? `Edit ${existing.name}` : "Add a connection"}>
      <div className="panel-head">
        <h2>{editing ? existing.name : "Add a connection"}</h2>
        <button type="button" className="btn btn-quiet" onClick={onClose}>
          Close
        </button>
      </div>

      {!editing && (
        <div className="paste">
          <label className="field">
            <span>Paste a connection string</span>
            <div className="row-inline">
              <input
                className="input mono"
                type="password"
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder="postgresql://user:password@host:5432/database"
                autoComplete="off"
                spellCheck={false}
              />
              <button type="button" className="btn" onClick={fillFromConnectionString} disabled={pasted.trim() === ""}>
                Fill in
              </button>
            </div>
            <span className="hint">Optional. Splits it into the fields below; it is not stored as typed.</span>
          </label>
          {pasteNote && (
            <div className={`note ${pasteNote.ok ? "live" : "fault"}`} role="status">
              {pasteNote.text}
            </div>
          )}
        </div>
      )}
      {!editing &&
        field(
          "Name",
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-invalid={nameProblem} autoComplete="off" />,
          nameProblem ? "Lower-case letters, digits, - and _ only. This is the name the AI passes as connection_name." : "What the AI calls it, e.g. shop-pg. It can't be changed later.",
        )}
      {field(
        "Engine",
        editing ? (
          <span>{info?.label ?? engine}</span>
        ) : (
          <select className="select" value={engine} onChange={(e) => setEngine(e.target.value)}>
            {engines.map((e) => (
              <option key={e.engine} value={e.engine} disabled={!e.available}>
                {e.label}
                {e.available ? "" : " (not installed)"}
              </option>
            ))}
          </select>
        ),
      )}
      {unavailable && (
        <div className="note fault" role="status">
          {unavailable.unavailable_reason}
        </div>
      )}
      {field("Description", <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />, "Shown to the AI, so say what data lives here.")}

      {isFile ? (
        field("File path", <input className="input mono" value={path} onChange={(e) => setPath(e.target.value)} />, "Opened read-only, on the machine the MCP server runs on.")
      ) : (
        <>
          {field("Host", <input className="input mono" value={host} onChange={(e) => setHost(e.target.value)} autoComplete="off" />)}
          {field("Port", <input className="input mono" inputMode="numeric" value={port} placeholder={text(info?.default_port)} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} />)}
          {field("Database", <input className="input mono" value={database} onChange={(e) => setDatabase(e.target.value)} autoComplete="off" />)}
          {field("User", <input className="input mono" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />, "Use a read-only database user. The MCP server also enforces read-only itself.")}
          {field("Schemas", <input className="input mono" value={schemas} onChange={(e) => setSchemas(e.target.value)} />, "Optional. Comma-separated; leave empty for the default schema.")}
          {field(
            "Driver options",
            <textarea className="input mono" rows={2} value={options} onChange={(e) => setOptions(e.target.value)} spellCheck={false} />,
            OPTION_HINTS[engine] ?? "Optional. One name=value per line. Passwords don't belong here.",
          )}
        </>
      )}

      {field(
        "Password",
        <input
          className="input"
          type="password"
          value={secret}
          autoComplete="new-password"
          placeholder={editing && existing.has_secret ? "Stored. Not shown." : ""}
          disabled={clearSecret}
          onChange={(e) => setSecret(e.target.value)}
        />,
        editing && existing.has_secret ? "Type a new password to replace the stored one. It is never displayed." : "Encrypted before it is stored, and never shown again.",
      )}
      {editing && existing.has_secret && (
        <label className="check">
          <input type="checkbox" checked={clearSecret} onChange={(e) => setClearSecret(e.target.checked)} />
          Remove the stored password
        </label>
      )}
      <label className="check">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Available to the MCP server
      </label>

      {test && (
        <div className={`note ${test.ok ? "live" : "fault"}`} role="status">
          {test.message}
          {test.ok && test.latency_ms !== null && ` (${test.latency_ms} ms)`}
        </div>
      )}
      {error && (
        <div className="note fault" role="alert">
          {error}
        </div>
      )}

      <div className="panel-actions">
        {editing && (
          <button type="button" className="btn btn-danger" onClick={onDelete}>
            Delete…
          </button>
        )}
        <span className="grow" />
        <button type="button" className="btn" onClick={runTest} disabled={busy !== null || !complete}>
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy !== null || !complete}>
          {busy === "save" ? "Saving…" : editing ? "Save changes" : "Add connection"}
        </button>
      </div>
    </form>
  );
}
