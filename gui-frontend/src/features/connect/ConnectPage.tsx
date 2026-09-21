import { useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../../api/client";
import type { ServerInfo } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { ErrorNote, PageHead, SegmentedControl } from "../../components/ui";
import { clientGuides, type ClientId } from "./clientGuides";

/** A block of text to copy, with a button that says it worked. */
function Copyable({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* the text is selectable: copying by hand still works */
    }
  }
  return (
    <div className="copyable">
      <pre tabIndex={0} aria-label={label}>
        {text}
      </pre>
      <button type="button" className="btn" onClick={() => void copy()} aria-label={`Copy ${label}`}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/**
 * How to point an AI client at this server. The address is the one thing that differs between
 * installations; everything else (sign in, then approve the agent) is the same everywhere.
 */
export function ConnectPage() {
  const api = useApi();
  const info = useAsync((signal) => api.get<ServerInfo>("/server-info", undefined, signal), [api]);
  const [client, setClient] = useState<ClientId>("claude");

  if (info.error) return <ErrorNote onRetry={info.reload}>{info.error}</ErrorNote>;
  if (!info.data)
    return (
      <p className="muted" role="status">
        Loading…
      </p>
    );

  const url = info.data.mcp_url;
  const guide = clientGuides(url ?? "https://your-server/mcp").find((g) => g.id === client)!;

  return (
    <>
      <PageHead title="Connect an agent" sub="Add this server to a chatbot or an AI tool, the same way you would add any other MCP server." />

      {url ? (
        <section className="card" aria-labelledby="address-title">
          <h2 id="address-title">This server&rsquo;s address</h2>
          <Copyable text={url} label="server address" />
          {!info.data.secure && (
            <div className="note fault" role="status" style={{ marginTop: 8 }}>
              This address is not HTTPS. Chatbots that run in the cloud can only connect to an HTTPS address that is reachable from the internet. Set <code>PUBLIC_HOST</code> to
              your host name (see &ldquo;Making it reachable&rdquo; below).
            </div>
          )}
          <p className="muted" style={{ marginBottom: 0 }}>
            People sign in with their normal account, at {info.data.issuer}.
          </p>
        </section>
      ) : (
        <div className="note fault" role="status">
          The server&rsquo;s public address isn&rsquo;t set, so the examples below use a placeholder. Set <code>GUI_MCP_PUBLIC_URL</code> for the admin API to the address agents will use, for
          example <code>https://mcp.example.com/mcp</code>.
        </div>
      )}

      <section className="card" aria-labelledby="how-title">
        <h2 id="how-title">What happens when an agent connects</h2>
        <ol className="steps">
          <li>You add the address to the chatbot or tool (below).</li>
          <li>The person using it is sent to the sign-in page and signs in with their own account.</li>
          <li>
            The agent connects. A pop-up appears here, on whatever screen you are on, asking whether to allow it. Until you do, it can&rsquo;t do anything.
          </li>
          <li>
            You choose what it may do (schema only, or full read access), which databases, and for how long. Change or withdraw that any time on <Link to="/agents">Agents</Link>.
          </li>
        </ol>
        <p className="muted" style={{ marginBottom: 0 }}>
          An approval is a ceiling. An agent can still only do what the person using it is allowed to do.
        </p>
      </section>

      <section className="card" aria-labelledby="client-title">
        <h2 id="client-title">Set it up in</h2>
        <SegmentedControl
          label="Client"
          value={client}
          onChange={setClient}
          options={clientGuides("").map((g) => ({ value: g.id, label: g.title }))}
        />
        <div className="guide" role="region" aria-label={`Setting up ${guide.title}`}>
          <ol className="steps">
            {guide.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          {guide.snippet && <Copyable text={guide.snippet.text} label={guide.snippet.label} />}
          {guide.note && <p className="muted">{guide.note}</p>}
        </div>
      </section>

      <section className="card" aria-labelledby="tell-title">
        <h2 id="tell-title">What the agent is told</h2>
        <p>
          The server describes itself to every agent: the tools, a good order of work, the limits, and what to say when something is refused. Agents can read it as an MCP resource, and
          anyone can read it{info.data.guide_url ? (
            <>
              {" "}
              at <a href={info.data.guide_url}>{info.data.guide_url}</a>
            </>
          ) : null}
          . Agents also have a <code>get_my_access</code> tool that tells them whether they are approved and what they can use.
        </p>
      </section>

      <section className="card" aria-labelledby="reach-title">
        <h2 id="reach-title">Making it reachable</h2>
        <ul className="plain">
          <li>
            <strong>Cloud chatbots</strong> connect from the provider&rsquo;s servers, so the address must be HTTPS and reachable from the internet. Set <code>PUBLIC_HOST</code> in <code>.env</code> to your
            host name; the bundled reverse proxy then gets a certificate for it.
          </li>
          <li>
            <strong>Trying it on your own machine?</strong> Use a tool on the same machine (Claude Code, VS Code, Cursor), or put a tunnel in front of <code>https://localhost</code> for a cloud chatbot.
          </li>
          <li>
            <strong>Who may register a client</strong> is limited to the hosts chatbots use. To allow another, add it to <code>KEYCLOAK_DCR_TRUSTED_HOSTS</code> and re-run the setup step.
          </li>
        </ul>
      </section>
    </>
  );
}
