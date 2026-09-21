import { useState, type FormEvent } from "react";

import { useApi } from "../../api/client";
import type { Connection, Report } from "../../api/types";

type ChartType = "" | "bar" | "line";

const chartOf = (report: Report | null) => {
  const c = report?.chart_config ?? {};
  return { type: (c.type as ChartType) ?? "", x: String(c.x ?? ""), y: String(c.y ?? "") };
};

/** Turn the three chart fields into the stored `chart_config`. No chart is an empty object. */
export function buildChartConfig(type: ChartType, x: string, y: string): Record<string, string> {
  return type ? { type, x: x.trim(), y: y.trim() } : {};
}

/** Add or edit one saved report. The SQL is checked by the server: only read-only queries are stored. */
export function ReportForm({
  existing,
  connections,
  onSaved,
  onClose,
  onDelete,
}: {
  existing: Report | null;
  connections: Connection[];
  onSaved: (saved: Report) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  const api = useApi();
  const initial = chartOf(existing);
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [connectionId, setConnectionId] = useState(existing?.connection_id ?? connections[0]?.id ?? "");
  const [sql, setSql] = useState(existing?.sql ?? "");
  const [chartType, setChartType] = useState<ChartType>(initial.type);
  const [x, setX] = useState(initial.x);
  const [y, setY] = useState(initial.y);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), description, connection_id: connectionId, sql, chart_config: buildChartConfig(chartType, x, y) };
    try {
      onSaved(existing ? await api.put<Report>(`/reports/${existing.id}`, body) : await api.post<Report>("/reports", body));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Saving failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={save} aria-label={existing ? `Edit ${existing.name}` : "Add a report"}>
      <div className="panel-head">
        <h2>{existing ? existing.name : "Add a report"}</h2>
        <button type="button" className="btn btn-quiet" onClick={onClose}>
          Close
        </button>
      </div>
      <label className="field">
        <span>Name</span>
        <input className="input" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Description</span>
        <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="field">
        <span>Database</span>
        <select className="select" value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.engine})
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>SQL</span>
        <textarea className="input mono" rows={7} value={sql} spellCheck={false} onChange={(e) => setSql(e.target.value)} />
        <span className="hint">A read-only query. Anything that writes is refused when you save.</span>
      </label>
      <label className="field">
        <span>Chart</span>
        <select className="select" value={chartType} onChange={(e) => setChartType(e.target.value as ChartType)}>
          <option value="">Table only</option>
          <option value="bar">Bar</option>
          <option value="line">Line</option>
        </select>
      </label>
      {chartType && (
        <div className="filters" style={{ margin: 0 }}>
          <label className="field grow">
            <span>X column</span>
            <input className="input mono" value={x} onChange={(e) => setX(e.target.value)} />
          </label>
          <label className="field grow">
            <span>Y column</span>
            <input className="input mono" value={y} onChange={(e) => setY(e.target.value)} />
          </label>
        </div>
      )}
      {error && (
        <div className="note fault" role="alert">
          {error}
        </div>
      )}
      <div className="panel-actions">
        {existing && (
          <button type="button" className="btn btn-danger" onClick={onDelete}>
            Delete…
          </button>
        )}
        <span className="grow" />
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || !sql.trim() || !connectionId}>
          {busy ? "Saving…" : existing ? "Save changes" : "Add report"}
        </button>
      </div>
    </form>
  );
}
