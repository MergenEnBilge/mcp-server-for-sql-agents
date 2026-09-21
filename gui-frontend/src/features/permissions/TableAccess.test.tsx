import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TableGrid } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { TableAccess } from "./TableAccess";

const grid: TableGrid = {
  connection_id: "c-1",
  connection_name: "shop-pg",
  reachable: true,
  error: null,
  tables: [
    { name: "orders", kind: "table", description: "" },
    { name: "payments", kind: "table", description: "" },
  ],
  subjects: [{ subject_type: "role", subject_id: "analyst", display_name: null, last_seen_at: null }],
  with_connection_access: [["role", "analyst"]],
  grants: [{ subject_type: "role", subject_id: "analyst", table: "orders" }],
};

const box = (name: string) => screen.getByRole("checkbox", { name });

describe("table permissions grid", () => {
  it("stages a grant without saving it, and says so", async () => {
    const { api, calls } = fakeApi({});
    renderScreen(<TableAccess grid={grid} onSaved={() => {}} />, api);
    expect(screen.getByText(/No unsaved changes/)).toBeInTheDocument();

    await userEvent.click(box("analyst can read payments"));
    expect(box("analyst can read payments")).toBeChecked();
    expect(box("analyst can read payments")).toHaveAttribute("data-pending", "true");
    expect(screen.getByText(/1 unsaved change/)).toBeInTheDocument();
    expect(calls).toHaveLength(0); // nothing is written until Save
  });

  it("toggling back leaves nothing to save", async () => {
    const { api } = fakeApi({});
    renderScreen(<TableAccess grid={grid} onSaved={() => {}} />, api);
    await userEvent.click(box("analyst can read payments"));
    await userEvent.click(box("analyst can read payments"));
    expect(screen.getByText(/No unsaved changes/)).toBeInTheDocument();
  });

  it("saves a pure grant straight away, in one call per subject", async () => {
    const put = vi.fn();
    const { api } = fakeApi({ "PUT /permissions/tables": (_p: unknown, body: unknown) => put(body) });
    const onSaved = vi.fn();
    renderScreen(<TableAccess grid={grid} onSaved={onSaved} />, api);
    await userEvent.click(box("analyst can read payments"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(put).toHaveBeenCalledWith({
      connection_id: "c-1",
      subject_type: "role",
      subject_id: "analyst",
      changes: [{ table: "payments", granted: true }],
    });
    expect(screen.getByText(/Saved 1 change/)).toBeInTheDocument();
  });

  it("names exactly what is lost before revoking anything", async () => {
    const put = vi.fn();
    const { api } = fakeApi({ "PUT /permissions/tables": (_p: unknown, body: unknown) => put(body) });
    renderScreen(<TableAccess grid={grid} onSaved={() => {}} />, api);
    await userEvent.click(box("analyst can read orders"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(put).not.toHaveBeenCalled(); // waiting for confirmation
    expect(await screen.findByText(/analyst will lose access to orders/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Revoke and save" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
  });

  it("shows why a save failed and keeps the staged changes", async () => {
    const { api } = fakeApi({
      "PUT /permissions/tables": () => {
        throw new Error("That connection doesn't exist.");
      },
    });
    renderScreen(<TableAccess grid={grid} onSaved={() => {}} />, api);
    await userEvent.click(box("analyst can read payments"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Not saved\./)).toBeInTheDocument();
    expect(box("analyst can read payments")).toBeChecked();
  });
});
