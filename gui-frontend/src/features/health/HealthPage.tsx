import { useEffect, useState, type ReactNode } from "react";

import { useApi } from "../../api/client";
import type { Check, Health } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatTimestamp, plural } from "../../components/format";
import { ErrorNote, PageHead, SegmentedControl } from "../../components/ui";
import { formatMs, formatRate } from "./chartMath";
import { TimeSeries } from "./TimeSeries";

const RANGES = [
  { value: "1", label: "1 hour" },
  { value: "24", label: "24 hours" },
  { value: "168", label: "7 days" },
];

const REFRESH_MS = 30_000;
const pad = (n: number) => String(n).padStart(2, "0");

/** Clock time for short windows; date and hour for long ones, where the day matters. */
export function timeLabelFor(hours: number) {
  return (iso: string) => {
    const d = new Date(iso);
    const clock = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return hours <= 24 ? clock : `${d.getDate()} ${d.toLocaleString("en-GB", { month: "short" })} ${clock}`;
  };
}

function Reading({ label, children, detail }: { label: string; children: ReactNode; detail?: ReactNode }) {
  return (
    <div className="reading">
      <dt>{label}</dt>
      <dd>{children}</dd>
      {detail && <div className="detail-line">{detail}</div>}
    </div>
  );
}

/** Up / down / not in use, in words. Green only ever means "answering right now". */
function CheckValue({ check, name }: { check: Check; name: string }) {
  if (check.state === "ok") {
    return (
      <>
        <span className="dot live" aria-hidden="true" />
        Up
        {check.latency_ms !== null && <span className="muted"> {check.latency_ms} ms</span>}
      </>
    );
  }
  if (check.state === "down") {
    return (
      <>
        <span className="dot fault" aria-hidden="true" />
        <span className="status-failed">Down</span>
        <span className="visually-hidden"> {name}</span>
      </>
    );
  }
  return <span className="muted">Not in use</span>;
}

export function HealthPage() {
  const api = useApi();
  const [hours, setHours] = useState("24");
  const health = useAsync((signal) => api.get<Health>("/health", { hours }, signal), [api, hours]);

  // A page people leave open should keep telling the truth.
  useEffect(() => {
    const timer = window.setInterval(health.reload, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [health.reload]);

  const h = health.data;
  const label = timeLabelFor(Number(hours));

  return (
    <>
      <PageHead
        title="Health"
        sub={h ? `Checked ${formatTimestamp(h.checked_at)}. Refreshes every 30 seconds.` : "Checking…"}
        actions={<SegmentedControl label="Period" value={hours} options={RANGES} onChange={setHours} />}
      />

      {health.error && !h ? (
        <ErrorNote onRetry={health.reload}>{health.error}</ErrorNote>
      ) : !h ? (
        <p className="muted" role="status">
          Checking the system…
        </p>
      ) : (
        <>
          {health.error && <ErrorNote onRetry={health.reload}>Could not refresh: {health.error} Showing the last reading.</ErrorNote>}
          <dl className="readings" aria-label="Current readings">
            <Reading label="Database" detail={h.database.detail}>
              <CheckValue check={h.database} name="database" />
            </Reading>
            <Reading label="Connection pool" detail={h.pool ? `${h.pool.idle} idle, ${h.pool.overflow} over the limit` : undefined}>
              {h.pool ? `${h.pool.in_use} of ${h.pool.size} in use` : <span className="muted">–</span>}
            </Reading>
            <Reading label="Redis" detail={h.redis.state === "ok" ? undefined : h.redis.detail}>
              <CheckValue check={h.redis} name="Redis" />
            </Reading>
            <Reading label="MCP server" detail={h.mcp_server.state === "ok" ? undefined : h.mcp_server.detail}>
              <CheckValue check={h.mcp_server} name="MCP server" />
            </Reading>
            <Reading label={`Failed calls, ${RANGES.find((r) => r.value === hours)?.label.toLowerCase()}`} detail={`${h.window.errors.toLocaleString("en-GB")} of ${plural(h.window.calls, "call")}`}>
              <span className={h.window.errors > 0 ? "" : "muted"}>{formatRate(h.window.error_rate)}</span>
            </Reading>
            <Reading label="Query time, 95th percentile" detail={`over ${plural(h.window.query_calls, "query", "queries")}`}>
              {formatMs(h.window.p95_query_ms)}
            </Reading>
          </dl>

          <div className="charts">
            <TimeSeries
              title="Calls"
              note="per interval"
              kind="columns"
              times={h.series.map((b) => b.start)}
              series={[
                { label: "Succeeded", tone: "quiet", values: h.series.map((b) => b.calls - b.errors) },
                { label: "Failed", tone: "fault", values: h.series.map((b) => b.errors) },
              ]}
              format={(v) => v.toLocaleString("en-GB")}
              timeLabel={label}
              dimmed={health.loading}
            />
            <TimeSeries
              title="Query time"
              note="95th percentile of run_query"
              kind="line"
              times={h.series.map((b) => b.start)}
              series={[{ label: "95th percentile", tone: "ink", values: h.series.map((b) => b.p95_query_ms) }]}
              format={(v) => formatMs(v)}
              timeLabel={label}
              dimmed={health.loading}
            />
          </div>

          <h2 className="section-title">Databases</h2>
          {h.connections.length === 0 ? (
            <div className="empty">No databases are registered.</div>
          ) : (
            <div className="table-wrap">
              <table className="data" aria-label="Database connections">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Engine</th>
                    <th scope="col">Last check</th>
                    <th scope="col">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {h.connections.map((c) => (
                    <tr key={c.name} className={c.last_check_ok === false ? "row failed" : "row"}>
                      <td>
                        {c.name}
                        {!c.is_active && <span className="chip" style={{ marginLeft: 6 }}>off</span>}
                      </td>
                      <td>{c.engine}</td>
                      <td>{c.last_checked_at ? formatTimestamp(c.last_checked_at) : <span className="muted">never</span>}</td>
                      <td>
                        {c.last_check_ok === null ? (
                          <span className="muted">Not checked. Use Test on the Connections screen.</span>
                        ) : c.last_check_ok ? (
                          <>
                            <span className="dot live" aria-hidden="true" />
                            Reachable
                          </>
                        ) : (
                          <span className="status-failed">{c.last_check_error ?? "Failed"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
