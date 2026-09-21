import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Health } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { HealthPage } from "./HealthPage";
import { TimeSeries } from "./TimeSeries";

const at = (minutes: number) => new Date(Date.UTC(2026, 8, 21, 12, minutes)).toISOString();

const health: Health = {
  checked_at: at(0),
  database: { state: "ok", detail: null, latency_ms: 3 },
  redis: { state: "down", detail: "Redis does not answer. Everything still works, without caching.", latency_ms: null },
  mcp_server: { state: "not_configured", detail: "GUI_MCP_HEALTH_URL is not set.", latency_ms: null },
  pool: { size: 10, in_use: 2, idle: 1, overflow: 0 },
  window: { hours: 24, calls: 96, errors: 2, error_rate: 2 / 96, query_calls: 60, p95_query_ms: 184 },
  series: [
    { start: at(0), calls: 10, errors: 0, p95_query_ms: 120 },
    { start: at(30), calls: 6, errors: 2, p95_query_ms: null },
    { start: at(60), calls: 0, errors: 0, p95_query_ms: null },
    { start: at(90), calls: 8, errors: 0, p95_query_ms: 184 },
  ],
  connections: [
    { name: "shop-pg", engine: "postgresql", is_active: true, last_checked_at: at(0), last_check_ok: true, last_check_error: null },
    { name: "legacy", engine: "mssql", is_active: true, last_checked_at: at(0), last_check_ok: false, last_check_error: "Connection failed: could not reach host old on port 1433." },
  ],
};

describe("health screen", () => {
  it("states each component in words, and says so when something is down", async () => {
    const { api } = fakeApi({ "GET /health": health });
    renderScreen(<HealthPage />, api);
    const readings = await screen.findByLabelText("Current readings");
    expect(within(readings).getByText("Down")).toBeInTheDocument();
    expect(within(readings).getByText(/Redis does not answer/)).toBeInTheDocument();
    expect(within(readings).getByText("Not in use")).toBeInTheDocument();
    expect(within(readings).getByText("2 of 10 in use")).toBeInTheDocument();
  });

  it("shows the failed-call rate and query time with what they were computed from", async () => {
    const { api } = fakeApi({ "GET /health": health });
    renderScreen(<HealthPage />, api);
    const readings = await screen.findByLabelText("Current readings");
    expect(within(readings).getByText("2.1%")).toBeInTheDocument();
    expect(within(readings).getByText("2 of 96 calls")).toBeInTheDocument();
    expect(within(readings).getByText("184 ms")).toBeInTheDocument();
    expect(within(readings).getByText("over 60 queries")).toBeInTheDocument();
  });

  it("does not report a zero failure rate when nothing was called", async () => {
    const quiet = { ...health, window: { ...health.window, calls: 0, errors: 0, error_rate: null, query_calls: 0, p95_query_ms: null } };
    const { api } = fakeApi({ "GET /health": quiet });
    renderScreen(<HealthPage />, api);
    expect(await screen.findByText("0 of 0 calls")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("asks for the period that was chosen", async () => {
    const { api, calls } = fakeApi({ "GET /health": health });
    renderScreen(<HealthPage />, api);
    await screen.findByText("2.1%");
    await userEvent.click(screen.getByRole("button", { name: "7 days" }));
    await screen.findByText("2.1%");
    expect(calls.map((c) => c.params?.hours)).toContain("168");
  });

  it("lists each database with the reason its last check failed", async () => {
    const { api } = fakeApi({ "GET /health": health });
    renderScreen(<HealthPage />, api);
    const table = await screen.findByRole("table", { name: "Database connections" });
    expect(within(table).getByText(/could not reach host old on port 1433/)).toBeInTheDocument();
  });
});

describe("time series chart", () => {
  const props = {
    title: "Calls",
    kind: "columns" as const,
    times: health.series.map((b) => b.start),
    series: [
      { label: "Succeeded", tone: "quiet" as const, values: [10, 4, 0, 8] },
      { label: "Failed", tone: "fault" as const, values: [0, 2, 0, 0] },
    ],
    format: (v: number) => String(v),
    timeLabel: (iso: string) => iso.slice(11, 16),
  };

  it("reads out every series at the point you arrow to, from the keyboard", async () => {
    renderScreen(<TimeSeries {...props} />, fakeApi({}).api);
    const chart = screen.getByRole("group", { name: /Calls/ });
    chart.focus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}"); // focus lands on the last point; two steps back is the second
    const tip = screen.getByRole("status");
    expect(tip).toHaveTextContent("12:30");
    expect(tip).toHaveTextContent("4Succeeded");
    expect(tip).toHaveTextContent("2Failed");
  });

  it("names both series in a legend, so colour is never the only clue", () => {
    renderScreen(<TimeSeries {...props} />, fakeApi({}).api);
    const legend = screen.getByLabelText("Legend");
    expect(legend).toHaveTextContent("Succeeded");
    expect(legend).toHaveTextContent("Failed");
  });

  it("offers the same numbers as a table", async () => {
    renderScreen(<TimeSeries {...props} />, fakeApi({}).api);
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = screen.getByRole("table", { name: "Calls, as a table" });
    expect(within(table).getAllByRole("row")).toHaveLength(5); // header + 4 points
    expect(within(table).getByRole("row", { name: /12:30/ })).toHaveTextContent("4");
  });

  it("says so when there is nothing to draw", () => {
    const flat = { ...props, series: props.series.map((s) => ({ ...s, values: [0, 0, 0, 0] })) };
    renderScreen(<TimeSeries {...flat} />, fakeApi({}).api);
    expect(screen.getByText("Nothing in this period")).toBeInTheDocument();
  });
});
