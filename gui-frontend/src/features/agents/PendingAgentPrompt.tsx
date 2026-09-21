import { useEffect, useRef, useState } from "react";

import { useApi } from "../../api/client";
import type { Agent, AgentAccess, AgentOptions } from "../../api/types";
import { formatTimestamp } from "../../components/format";
import { AccessForm } from "./AccessForm";

/** The facts about a request that help an administrator decide. */
export function AgentFacts({ agent }: { agent: Agent }) {
  return (
    <dl className="facts">
      <dt>It calls itself</dt>
      <dd>
        {agent.reported_name ?? <span className="muted">(it didn&rsquo;t say)</span>} <span className="muted">not verified</span>
      </dd>
      <dt>Client id</dt>
      <dd className="mono">{agent.client_id}</dd>
      <dt>Signed in as</dt>
      <dd>
        {agent.last_user_name ?? "(no name)"} <span className="mono muted">{agent.last_user_sub}</span>
      </dd>
      <dt>First seen</dt>
      <dd>{formatTimestamp(agent.first_seen_at)}</dd>
    </dl>
  );
}

/**
 * The pop-up that appears, on any screen, when an AI client connects for the first time. The
 * administrator allows it (choosing what it may do), blocks it, or decides later. Until then the
 * agent is refused everything and is told an administrator has to approve it.
 */
export function PendingAgentPrompt({ agents, onDecided }: { agents: Agent[]; onDecided: () => void }) {
  const api = useApi();
  const ref = useRef<HTMLDialogElement>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [options, setOptions] = useState<AgentOptions | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const waiting = agents.filter((a) => !dismissed.has(a.id));
  const current = waiting[0] ?? null;

  useEffect(() => {
    if (current && !options) {
      api
        .get<AgentOptions>("/agents/options")
        .then(setOptions)
        .catch(() => setError("The choices couldn't be loaded. Try again from the Agents screen."));
    }
  }, [current, options, api]);

  const open = current !== null && options !== null;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function later() {
    if (current) setDismissed((d) => new Set(d).add(current.id));
    setError(null);
  }

  async function decide(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className="modal agent-prompt" aria-labelledby="agent-prompt-title" onClose={later}>
      {current && options && (
        <>
          <div className="modal-body">
            <h2 id="agent-prompt-title">A new agent wants to connect</h2>
            {waiting.length > 1 && <p className="muted">1 of {waiting.length} waiting for a decision.</p>}
            <AgentFacts agent={current} />
            <p>Until you decide, it can&rsquo;t do anything: it is told an administrator has to approve it first.</p>
            <AccessForm
              key={current.id}
              options={options}
              submitLabel="Allow"
              busy={busy}
              error={error}
              onSubmit={(access: AgentAccess) => void decide(() => api.post(`/agents/${current.id}/approve`, access))}
              secondary={
                <>
                  <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void decide(() => api.post(`/agents/${current.id}/block`))}>
                    Block
                  </button>
                  <button type="button" className="btn" disabled={busy} onClick={later}>
                    Decide later
                  </button>
                </>
              }
            />
          </div>
        </>
      )}
    </dialog>
  );
}
