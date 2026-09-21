import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Connection, EngineInfo } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { buildDetails } from "./ConnectionForm";
import { ConnectionsPage } from "./ConnectionsPage";

const engines: EngineInfo[] = [
  { engine: "postgresql", label: "PostgreSQL", default_port: 5432, required: ["host", "database", "username"] },
  { engine: "sqlite", label: "SQLite (file)", default_port: null, required: ["path"] },
];

const shop: Connection = {
  id: "c-1",
  name: "shop-pg",
  engine: "postgresql",
  description: "The shop",
  details: { host: "db.internal", port: 5432, database: "shop", username: "readonly" },
  has_secret: true,
  is_active: true,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  last_checked_at: "2026-09-21T09:00:00Z",
  last_check_ok: false,
  last_check_error: "Connection failed: could not reach host db.internal on port 5432.",
  access_count: 2,
};

const routes = (extra: Record<string, unknown> = {}) => ({ "GET /connections": [shop], "GET /engines": engines, ...extra });

describe("connection manager", () => {
  it("shows the state of the last check in words, with the reason", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    const row = (await screen.findByRole("button", { name: "shop-pg" })).closest("tr")!;
    expect(row).toHaveTextContent("Failed");
    expect(row).toHaveTextContent("could not reach host db.internal on port 5432");
    expect(row).toHaveTextContent("2 users and roles");
  });

  it("never displays the stored password, and only sends one if a new one is typed", async () => {
    const put = vi.fn();
    const { api } = fakeApi(routes({ "PUT /connections/c-1": (_p: unknown, body: unknown) => (put(body), shop) }));
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "shop-pg" }));

    const password = screen.getByLabelText(/^Password/);
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", "Stored. Not shown.");

    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0]![0]).toMatchObject({ secret: null, clear_secret: false });

    await userEvent.type(password, "new-password");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls[1]![0]).toMatchObject({ secret: "new-password" });
  });

  it("refuses a name the API would refuse, before asking", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    await userEvent.type(screen.getByLabelText(/^Name/), "Shop DB");
    expect(screen.getByText(/Lower-case letters, digits/)).toBeInTheDocument();
    const [, submit] = screen.getAllByRole("button", { name: "Add connection" }); // header button, then the form's
    expect(submit).toBeDisabled();
  });

  it("asks for a file path, not a host, for SQLite", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    await userEvent.selectOptions(screen.getByLabelText("Engine"), "sqlite");
    expect(screen.getByLabelText(/File path/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Host/)).not.toBeInTheDocument();
  });

  it("reports a failed test in the interface's own words", async () => {
    const { api } = fakeApi(
      routes({ "POST /connections/test": { ok: false, message: "Connection failed: authentication failed for user 'readonly'.", table_count: null, latency_ms: null } }),
    );
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "shop-pg" }));
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/authentication failed for user 'readonly'/)).toBeInTheDocument();
  });

  it("spells out what deleting a connection removes", async () => {
    const del = vi.fn();
    const { api } = fakeApi(routes({ "DELETE /connections/c-1": () => del() }));
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "shop-pg" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(await screen.findByText(/stops offering shop-pg to anyone/)).toBeInTheDocument();
    expect(screen.getByText(/2 access grants/)).toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete connection" }));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });
});

describe("buildDetails", () => {
  const blank = { host: "", port: "", database: "", username: "", path: "", schemas: "" };

  it("sends a port only when one was typed, and as a number", () => {
    expect(buildDetails("postgresql", { ...blank, host: " h ", database: "d", username: "u" })).toEqual({ host: "h", database: "d", username: "u" });
    expect(buildDetails("postgresql", { ...blank, host: "h", port: "5433", database: "d", username: "u" })).toMatchObject({ port: 5433 });
  });

  it("splits schemas on commas and ignores blanks", () => {
    expect(buildDetails("postgresql", { ...blank, host: "h", database: "d", username: "u", schemas: "sales, , hr" })).toMatchObject({ schemas: ["sales", "hr"] });
  });

  it("uses only the path for SQLite", () => {
    expect(buildDetails("sqlite", { ...blank, host: "ignored", path: "/data/x.sqlite" })).toEqual({ path: "/data/x.sqlite" });
  });
});
