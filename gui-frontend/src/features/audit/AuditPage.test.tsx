import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AuditItem } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { AuditPage } from "./AuditPage";

const item = (over: Partial<AuditItem>): AuditItem => ({
  id: 1,
  occurred_at: "2026-09-21T14:02:11Z",
  caller_sub: "u-ana",
  caller_name: "Ana Analyst",
  tool_name: "run_query",
  connection_name: "shop-pg",
  tables: ["orders"],
  sql_preview: "SELECT * FROM orders",
  sql_truncated: false,
  success: true,
  error_preview: null,
  row_count: 42,
  duration_ms: 18,
  ...over,
});

const facets = { callers: [], tools: ["run_query"], connections: ["shop-pg"], tables: ["orders"] };

function page(items: AuditItem[]) {
  return { items, total: items.length, page: 1, page_size: 50 };
}

describe("audit log screen", () => {
  it("says in words when a call failed, not only with colour", async () => {
    const { api } = fakeApi({
      "GET /audit/facets": facets,
      "GET /audit": page([item({ id: 1 }), item({ id: 2, success: false, error_preview: "table not allowed" })]),
    });
    renderScreen(<AuditPage />, api);
    const table = await screen.findByRole("table", { name: "Audit log" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).not.toHaveClass("failed");
    expect(rows[1]).toHaveClass("failed");
    expect(within(rows[1]!).getByText("failed")).toBeInTheDocument();
  });

  it("expands a row to show the full SQL and the error", async () => {
    const detail = {
      ...item({ id: 2, success: false }),
      arguments: { sql: "SELECT secret FROM users" },
      sql: "SELECT secret FROM users",
      result_summary: null,
      error_message: "Table 'users' is not allowed.",
    };
    const { api } = fakeApi({
      "GET /audit/facets": facets,
      "GET /audit": page([item({ id: 2, success: false })]),
      "GET /audit/2": detail,
    });
    renderScreen(<AuditPage />, api);
    await userEvent.click(await screen.findByText("Ana Analyst"));
    expect(await screen.findByText("Table 'users' is not allowed.")).toBeInTheDocument();
    expect(screen.getAllByText("SELECT secret FROM users").length).toBeGreaterThan(0);
  });

  it("filters by outcome and asks the API for exactly that", async () => {
    const { api, calls } = fakeApi({ "GET /audit/facets": facets, "GET /audit": page([item({})]) });
    renderScreen(<AuditPage />, api);
    await screen.findByRole("table", { name: "Audit log" });
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "failed");
    await waitFor(() => expect(calls.some((c) => c.path === "/audit" && c.params?.success === false)).toBe(true));
  });

  it("speaks plainly when nothing matches", async () => {
    const { api } = fakeApi({ "GET /audit/facets": facets, "GET /audit": page([]) });
    renderScreen(<AuditPage />, api, "/?tool=run_query");
    expect(await screen.findByText(/No audit entries match these filters/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear filters" }).length).toBeGreaterThan(0);
  });

  it("shows a failure to load instead of an empty table", async () => {
    const { api } = fakeApi({
      "GET /audit/facets": facets,
      "GET /audit": () => {
        throw new Error("Could not reach the server. Check your connection and try again.");
      },
    });
    renderScreen(<AuditPage />, api);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server");
  });
});
