import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Connection, EngineInfo } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { buildDetails } from "./ConnectionForm";
import { ConnectionsPage } from "./ConnectionsPage";

const engines: EngineInfo[] = [
  { engine: "postgresql", label: "PostgreSQL", default_port: 5432, required: ["host", "database", "username"], available: true, unavailable_reason: null },
  { engine: "sqlite", label: "SQLite (file)", default_port: null, required: ["path"], available: true, unavailable_reason: null },
  { engine: "mssql", label: "SQL Server", default_port: 1433, required: ["host", "database", "username"], available: false, unavailable_reason: "Microsoft's ODBC driver for SQL Server isn't installed in this server." },
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

describe("adapters and driver options", () => {
  it("offers only engines this server can open, and says why the others can't be used", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    expect(screen.getByRole("option", { name: "SQL Server (not installed)" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "PostgreSQL" })).toBeEnabled();
  });

  it("fills the fields from a pasted connection string and moves the password out of sight", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));

    await userEvent.type(screen.getByLabelText(/Paste a connection string/), "postgresql://reader:p%40ss@db.internal:5433/shop?ssl=require");
    await userEvent.click(screen.getByRole("button", { name: "Fill in" }));

    expect(screen.getByLabelText(/^Host/)).toHaveValue("db.internal");
    expect(screen.getByLabelText(/^Port/)).toHaveValue("5433");
    expect(screen.getByLabelText(/^Database/)).toHaveValue("shop");
    expect(screen.getByLabelText(/^User/)).toHaveValue("reader");
    expect(screen.getByLabelText(/^Driver options/)).toHaveValue("ssl=require");
    expect(screen.getByLabelText(/^Password/)).toHaveValue("p@ss");
    expect(screen.getByLabelText(/Paste a connection string/)).toHaveValue(""); // not left on screen
    expect(screen.getByText(/password went into the password field/)).toBeInTheDocument();
  });

  it("says what is wrong with a connection string it can't read", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    await userEvent.type(screen.getByLabelText(/Paste a connection string/), "oracle://u:p@h/db");
    await userEvent.click(screen.getByRole("button", { name: "Fill in" }));
    expect(await screen.findByText(/isn't an engine this console knows/)).toBeInTheDocument();
  });

  it("sends driver options with the settings", async () => {
    const post = vi.fn();
    const { api } = fakeApi(routes({ "POST /connections/test": (_p: unknown, body: unknown) => (post(body), { ok: true, message: "Connected.", table_count: 1, latency_ms: 3 }) }));
    renderScreen(<ConnectionsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    await userEvent.type(screen.getByLabelText(/^Name/), "shop-pg");
    await userEvent.type(screen.getByLabelText(/^Host/), "h");
    await userEvent.type(screen.getByLabelText(/^Database/), "d");
    await userEvent.type(screen.getByLabelText(/^User/), "u");
    await userEvent.type(screen.getByLabelText(/^Driver options/), "ssl=require");
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0].details.options).toEqual({ ssl: "require" });
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

  it("includes driver options only when there are some", () => {
    const base = { ...blank, host: "h", database: "d", username: "u" };
    expect(buildDetails("postgresql", base)).not.toHaveProperty("options");
    expect(buildDetails("postgresql", { ...base, options: "ssl=require\n\nfoo = bar " })).toMatchObject({ options: { ssl: "require", foo: "bar" } });
  });

  it("uses only the path for SQLite", () => {
    expect(buildDetails("sqlite", { ...blank, host: "ignored", path: "/data/x.sqlite" })).toEqual({ path: "/data/x.sqlite" });
  });
});
