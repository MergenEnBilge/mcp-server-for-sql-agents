import { useState } from "react";

import type { AgentAccess, AgentOptions } from "../../api/types";

/** How long an approval lasts. `null` means until someone changes it. */
export const DURATIONS: { hours: number | null; label: string }[] = [
  { hours: 1, label: "1 hour" },
  { hours: 24, label: "1 day" },
  { hours: 24 * 7, label: "7 days" },
  { hours: 24 * 30, label: "30 days" },
  { hours: null, label: "No end date" },
];

type Preset = "explorer" | "analyst" | "custom";

const PRESET_TEXT: Record<Exclude<Preset, "custom">, { title: string; detail: string }> = {
  explorer: { title: "Schema only", detail: "Can learn which databases, tables and columns exist. Never sees a row of data." },
  analyst: { title: "Full read access", detail: "Everything above, plus running read-only queries and looking at sample rows." },
};

/** Which preset a list of tools happens to match, so an existing approval opens on the right choice. */
export function presetFor(tools: string[], options: AgentOptions): Preset {
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((t) => b.includes(t));
  if (same(tools, options.presets.explorer ?? [])) return "explorer";
  if (same(tools, options.presets.analyst ?? [])) return "analyst";
  return "custom";
}

/**
 * The choices an administrator makes about an agent: which tools, which databases, for how long.
 * An approval is a ceiling: the agent can still only do what the person using it may do.
 */
export function AccessForm({
  options,
  initial,
  submitLabel,
  busy,
  error,
  showClientId,
  onSubmit,
  secondary,
}: {
  options: AgentOptions;
  initial?: Partial<AgentAccess> & { tools?: string[] };
  submitLabel: string;
  busy: boolean;
  error: string | null;
  /** Pre-approving needs the client id typed in; approving an existing agent doesn't. */
  showClientId?: boolean;
  onSubmit: (access: AgentAccess, clientId: string) => void;
  /** Extra buttons next to the submit button (block, decide later, ...). */
  secondary?: React.ReactNode;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [clientId, setClientId] = useState("");
  const startTools = initial?.tools ?? options.presets.explorer ?? [];
  const [preset, setPreset] = useState<Preset>(presetFor(startTools, options));
  const [custom, setCustom] = useState<string[]>(startTools);
  const [allConnections, setAllConnections] = useState(initial?.all_connections ?? true);
  const [chosen, setChosen] = useState<string[]>(initial?.connection_ids ?? []);
  const [hours, setHours] = useState<number | null>(initial?.expires_in_hours === undefined ? 24 * 30 : initial.expires_in_hours);

  const tools = preset === "custom" ? custom : (options.presets[preset] ?? []);
  const toggle = (list: string[], set: (v: string[]) => void, value: string) => set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  const valid = tools.length > 0 && (allConnections || chosen.length > 0) && (!showClientId || clientId.trim() !== "");

  return (
    <form
      className="access-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSubmit({ label, tools, all_connections: allConnections, connection_ids: allConnections ? [] : chosen, expires_in_hours: hours }, clientId.trim());
      }}
    >
      {showClientId && (
        <label className="field">
          <span>Client id</span>
          <input className="input mono" value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" />
          <span className="hint">The OAuth client id the agent signs in with (the identity provider shows it).</span>
        </label>
      )}
      <label className="field">
        <span>Name</span>
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={100} placeholder="e.g. Pat's chatbot" />
        <span className="hint">Only for you, to recognise it later.</span>
      </label>

      <fieldset className="choice">
        <legend>What it may do</legend>
        {(["explorer", "analyst"] as const).map((name) => (
          <label key={name} className="option">
            <input type="radio" name="preset" checked={preset === name} onChange={() => setPreset(name)} />
            <span>
              <strong>{PRESET_TEXT[name].title}</strong>
              <span className="hint">{PRESET_TEXT[name].detail}</span>
            </span>
          </label>
        ))}
        <label className="option">
          <input type="radio" name="preset" checked={preset === "custom"} onChange={() => setPreset("custom")} />
          <span>
            <strong>Choose tools</strong>
          </span>
        </label>
        {preset === "custom" && (
          <div className="tool-list">
            {options.tools.map((tool) => (
              <label key={tool.name} className="check" title={tool.description}>
                <input type="checkbox" checked={custom.includes(tool.name)} onChange={() => toggle(custom, setCustom, tool.name)} />
                <span className="mono">{tool.name}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <fieldset className="choice">
        <legend>Which databases</legend>
        <label className="option">
          <input type="radio" name="scope" checked={allConnections} onChange={() => setAllConnections(true)} />
          <span>Every database the person using it may use</span>
        </label>
        <label className="option">
          <input type="radio" name="scope" checked={!allConnections} onChange={() => setAllConnections(false)} />
          <span>Only these</span>
        </label>
        {!allConnections && (
          <div className="tool-list">
            {options.connections.length === 0 && <span className="muted">No databases are registered yet.</span>}
            {options.connections.map((c) => (
              <label key={c.id} className="check">
                <input type="checkbox" checked={chosen.includes(c.id)} onChange={() => toggle(chosen, setChosen, c.id)} />
                <span className="mono">{c.name}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <label className="field">
        <span>How long</span>
        <select className="select" value={hours === null ? "none" : String(hours)} onChange={(e) => setHours(e.target.value === "none" ? null : Number(e.target.value))}>
          {DURATIONS.map((d) => (
            <option key={d.label} value={d.hours === null ? "none" : String(d.hours)}>
              {d.label}
            </option>
          ))}
        </select>
      </label>

      <p className="muted access-note">
        This is a ceiling. The agent can still only do what the person using it is allowed to do, and only the databases and tables that person has been granted.
      </p>

      {error && (
        <div className="note fault" role="alert">
          {error}
        </div>
      )}
      <div className="panel-actions">
        {secondary}
        <span className="grow" />
        <button type="submit" className="btn btn-primary" disabled={busy || !valid}>
          {busy ? "Saving…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
