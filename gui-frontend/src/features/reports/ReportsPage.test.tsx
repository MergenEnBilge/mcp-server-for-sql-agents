import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Connection, Report } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { buildChartConfig } from "./ReportForm";
import { ReportsPage } from "./ReportsPage";

const connection = { id: "c-1", name: "shop-pg", engine: "postgresql" } as Connection;

const report: Report = {
  id: "r-1",
  name: "Customers by country",
  description: "Head count",
  connection_id: "c-1",
  connection_name: "shop-pg",
  sql: "SELECT country, count(*) FROM customers GROUP BY country",
  chart_config: { type: "bar", x: "country", y: "count" },
  owner_sub: "u-ada",
  owner_name: "Ada Admin",
  created_at: "2026-09-21T10:00:00Z",
  updated_at: "2026-09-21T10:00:00Z",
};

describe("saved reports", () => {
  it("lists reports with their database, chart and owner", async () => {
    const { api } = fakeApi({ "GET /reports": [report], "GET /connections": [connection] });
    renderScreen(<ReportsPage />, api);
    const row = (await screen.findByRole("button", { name: "Customers by country" })).closest("tr")!;
    expect(row).toHaveTextContent("shop-pg");
    expect(row).toHaveTextContent("bar");
    expect(row).toHaveTextContent("Ada Admin");
  });

  it("says plainly when there is nothing yet, and when a search finds nothing", async () => {
    const { api } = fakeApi({ "GET /reports": [], "GET /connections": [connection] });
    renderScreen(<ReportsPage />, api);
    expect(await screen.findByText("No reports have been saved yet.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search reports"), "zzz");
    expect(await screen.findByText("No reports match that search.")).toBeInTheDocument();
  });

  it("can't add a report until a database exists", async () => {
    const { api } = fakeApi({ "GET /reports": [], "GET /connections": [] });
    renderScreen(<ReportsPage />, api);
    expect(await screen.findByText(/Register a database first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add report" })).toBeDisabled();
  });

  it("creates a report and sends the chart settings the way the API stores them", async () => {
    const post = vi.fn();
    const { api } = fakeApi({
      "GET /reports": [],
      "GET /connections": [connection],
      "POST /reports": (_p: unknown, body: unknown) => (post(body), report),
    });
    renderScreen(<ReportsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add report" }));
    await userEvent.type(screen.getByLabelText("Name"), "Customers by country");
    await userEvent.type(screen.getByLabelText(/^SQL/), "SELECT 1");
    await userEvent.selectOptions(screen.getByLabelText("Chart"), "line");
    await userEvent.type(screen.getByLabelText("X column"), "day");
    await userEvent.type(screen.getByLabelText("Y column"), "total");
    const [, submit] = screen.getAllByRole("button", { name: "Add report" }); // header button, then the form's
    await userEvent.click(submit!);
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toEqual({
      name: "Customers by country",
      description: "",
      connection_id: "c-1",
      sql: "SELECT 1",
      chart_config: { type: "line", x: "day", y: "total" },
    });
  });

  it("shows the server's reason when the SQL is refused", async () => {
    const { api } = fakeApi({
      "GET /reports": [report],
      "GET /connections": [connection],
      "PUT /reports/r-1": () => {
        throw new Error("This isn't a read-only query: Only SELECT statements are allowed.");
      },
    });
    renderScreen(<ReportsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Customers by country" }));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("isn't a read-only query");
  });
});

describe("buildChartConfig", () => {
  it("stores nothing for a table-only report", () => {
    expect(buildChartConfig("", "a", "b")).toEqual({});
  });
  it("trims the column names", () => {
    expect(buildChartConfig("bar", " day ", "total ")).toEqual({ type: "bar", x: "day", y: "total" });
  });
});
