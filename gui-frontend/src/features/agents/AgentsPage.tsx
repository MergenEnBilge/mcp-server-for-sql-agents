import { useState } from "react";

import { useApi } from "../../api/client";
import type { Agent, AgentAccess, AgentOptions, AgentState } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatTimestamp, plural } from "../../components/format";
import { ConfirmDialog, ErrorNote, PageHead } from "../../components/ui";
import { AccessForm } from "./AccessForm";
import { AgentFacts } from "./PendingAgentPrompt";

const STATE_TEXT: Record<AgentState, string> = {
  pending: "Waiting for a decision",
  approved: "Approved",
  blocked: "Blocked",
  expired: "Approval ended",
};

function StateMark({ state }: { state: AgentState }) {
  const kind = state === "approved" ? "live" : state === "pending" ? "pending" : state === "blocked" ? "fault" : "";
  return (
    <>
      <span className={`dot ${kind}`} aria-hidden="true" />
      {state === "blocked" ? <span className="status-failed">{STATE_TEXT[state]}</span> : STATE_TEXT[state]}
    </>
  );
}

function scopeText(a: Agent): string {
  if (a.state === "pending" || a.state === "blocked") return "–";
  const tools = plural(a.allowed_tools.length, "tool");
  return `${tools}, ${a.all_connections ? "all databases" : a.connections.join(", ")}`;
}

/** Every AI client that has connected, with what it was allowed and what it has been doing. */
export function AgentsPage({ onChanged }: { onChanged?: () => void }) {
  const api = useApi();
  const agents = useAsync((signal) => api.get<Agent[]>("/agents", undefined, signal), [api]);
  const options = useAsync((signal) => api.get<AgentOptions>("/agents/options", undefined, signal), [api]);

  const [panel, setPanel] = useState<"new" | string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"block" | "remove" | null>(null);

  const selected = agents.data?.find((a) => a.id === panel) ?? null;

  const changed = (text: string) => {
    setMessage(text);
    agents.reload();
    onChanged?.();
  };

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      changed(done);
      setConfirm(null);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
      setConfirm(null);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const name = (a: Agent) => a.label || a.reported_name || a.client_id;

  return (
    <>
      <PageHead
        title="Agents"
        sub="The AI clients that connect to the MCP server. A new one can do nothing until you approve it, and an approval only ever narrows what the person using it may do."
        actions={
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setPanel("new");
              setMessage(null);
              setError(null);
            }}
          >
            Approve in advance
          </button>
        }
      />
      {message && (
        <div className="note" role="status" style={{ marginBottom: 12 }}>
          {message}
        </div>
      )}

      {agents.error || options.error ? (
        <ErrorNote onRetry={() => (agents.reload(), options.reload())}>{agents.error ?? options.error}</ErrorNote>
      ) : !agents.data || !options.data ? (
        <p className="muted" role="status">
          Loading agents…
        </p>
      ) : (
        <div className={`split${panel ? " with-panel" : ""}`}>
          <div>
            {agents.data.length === 0 ? (
              <div className="empty">
                No agent has connected yet. When one does, a prompt appears here so you can decide what it may do. Or approve one in advance with its client id.
              </div>
            ) : (
              <div className="table-wrap">
                <table className="data" aria-label="Agents">
                  <thead>
                    <tr>
                      <th scope="col">Agent</th>
                      <th scope="col">State</th>
                      <th scope="col">Allowed</th>
                      <th scope="col">Last seen</th>
                      <th scope="col">Calls today</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agents.data.map((a) => (
                      <tr key={a.id} className={`row${a.id === panel ? " open" : ""}${a.state === "blocked" ? " failed" : ""}`}>
                        <td>
                          <button type="button" className="link" onClick={() => (setPanel(a.id), setMessage(null), setError(null))}>
                            {name(a)}
                          </button>
                          {a.last_user_name && <div className="muted clip">for {a.last_user_name}</div>}
                        </td>
                        <td>
                          <StateMark state={a.state} />
                          {a.state === "approved" && a.expires_at && <div className="muted">until {formatTimestamp(a.expires_at)}</div>}
                        </td>
                        <td className="clip" title={scopeText(a)}>
                          {scopeText(a)}
                        </td>
                        <td>{formatTimestamp(a.last_seen_at)}</td>
                        <td>{a.calls_24h.toLocaleString("en-GB")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {panel === "new" && (
            <section className="panel" aria-label="Approve an agent in advance">
              <div className="panel-head">
                <h2>Approve in advance</h2>
                <button type="button" className="btn btn-quiet" onClick={() => setPanel(null)}>
                  Close
                </button>
              </div>
              <AccessForm
                options={options.data}
                showClientId
                submitLabel="Approve"
                busy={busy}
                error={error}
                onSubmit={(access: AgentAccess, clientId: string) =>
                  void run(async () => {
                    const created = await api.post<Agent>("/agents", { ...access, client_id: clientId });
                    setPanel(created.id);
                  }, `Approved ${clientId}. It can connect straight away.`)
                }
              />
            </section>
          )}

          {selected && (
            <section className="panel" aria-label={`Agent ${name(selected)}`}>
              <div className="panel-head">
                <h2>{name(selected)}</h2>
                <button type="button" className="btn btn-quiet" onClick={() => setPanel(null)}>
                  Close
                </button>
              </div>
              <AgentFacts agent={selected} />
              <p>
                <StateMark state={selected.state} />
                {selected.decided_by && selected.decided_at && (
                  <span className="muted">
                    {" "}
                    · decided {formatTimestamp(selected.decided_at)} by {selected.decided_by}
                  </span>
                )}
              </p>
              <h3 className="panel-sub">{selected.state === "pending" || selected.state === "blocked" ? "Allow it" : "What it may do"}</h3>
              <AccessForm
                key={`${selected.id}-${selected.decided_at}`}
                options={options.data}
                initial={{
                  label: selected.label,
                  tools: selected.allowed_tools.length ? selected.allowed_tools : undefined,
                  all_connections: selected.all_connections,
                  connection_ids: options.data.connections.filter((c) => selected.connections.includes(c.name)).map((c) => c.id),
                  expires_in_hours: selected.state === "approved" && !selected.expires_at ? null : undefined,
                }}
                submitLabel={selected.state === "approved" ? "Save changes" : selected.state === "expired" ? "Renew" : "Allow"}
                busy={busy}
                error={error}
                onSubmit={(access) => void run(() => api.post(`/agents/${selected.id}/approve`, access), `Saved. ${name(selected)} uses the new limits from its next request.`)}
                secondary={
                  <>
                    <button type="button" className="btn btn-danger" onClick={() => setConfirm("remove")} disabled={busy}>
                      Remove…
                    </button>
                    {selected.state !== "blocked" && (
                      <button type="button" className="btn" onClick={() => setConfirm("block")} disabled={busy}>
                        Block…
                      </button>
                    )}
                  </>
                }
              />
            </section>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirm === "block"}
        title={`Block ${selected ? name(selected) : "this agent"}?`}
        confirmLabel="Block agent"
        destructive
        busy={busy}
        onConfirm={() => selected && void run(() => api.post(`/agents/${selected.id}/block`), `Blocked ${name(selected)}. It is refused from its next request.`)}
        onCancel={() => setConfirm(null)}
      >
        <p>It is refused everything from its next request on, and told an administrator blocked it. You can allow it again later.</p>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirm === "remove"}
        title={`Remove ${selected ? name(selected) : "this agent"}?`}
        confirmLabel="Remove agent"
        destructive
        busy={busy}
        onConfirm={() =>
          selected &&
          void run(async () => {
            await api.del(`/agents/${selected.id}`);
            setPanel(null);
          }, `Removed ${name(selected)}.`)
        }
        onCancel={() => setConfirm(null)}
      >
        <p>It is forgotten. If it connects again, it appears as a new request waiting for a decision. Its past calls stay in the audit log.</p>
      </ConfirmDialog>
    </>
  );
}
