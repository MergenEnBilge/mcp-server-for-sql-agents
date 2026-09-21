import { expect, test } from "@playwright/test";

import { signIn } from "./helpers";

test.describe("audit log", () => {
  test("an administrator can read, filter and expand the log", async ({ page }) => {
    await signIn(page, "alice", "/audit");

    // The shell says who is signed in, permanently.
    await expect(page.locator(".rail-identity")).toContainText("Alice Admin");
    await expect(page.locator(".rail-identity")).toContainText("Administrator");

    const table = page.getByRole("table", { name: "Audit log" });
    await expect(table.locator("tbody tr.row").first()).toBeVisible();
    await page.screenshot({ path: "test-results/audit-1-overview.png" });

    // A failed call is marked in words, not only with the red edge.
    const failed = table.locator("tr.row.failed").first();
    await expect(failed).toContainText("failed");

    // Filter down to refused calls, then open one.
    await page.getByLabel("Outcome").selectOption("failed");
    await expect(table.locator("tr.row").first()).toBeVisible();
    for (const row of await table.locator("tr.row").all()) await expect(row).toHaveClass(/failed/);
    await table.locator("tr.row").first().click();
    await expect(page.locator(".detail")).toContainText("Error");
    await page.screenshot({ path: "test-results/audit-2-failed-expanded.png" });
  });

  test("filters live in the address bar and can be cleared", async ({ page }) => {
    await signIn(page, "alice", "/audit?tool=run_query");
    await expect(page.getByLabel("Tool")).toHaveValue("run_query");
    for (const cell of await page.locator("tbody tr.row td:nth-child(4)").all()) await expect(cell).toHaveText("run_query");
    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(page).not.toHaveURL(/tool=/);
  });

  test("keyboard navigation moves between rows and expands one", async ({ page }) => {
    await signIn(page, "alice", "/audit");
    const rows = page.locator("tbody tr.row");
    await rows.first().focus();
    await page.keyboard.press("ArrowDown");
    await expect(rows.nth(1)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator(".detail")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".detail")).toHaveCount(0);
    await page.keyboard.press("/");
    // "/" is only a shortcut when no field has focus; focus a row first.
    await rows.first().focus();
    await page.keyboard.press("/");
    await expect(page.getByLabel("Search")).toBeFocused();
  });

  test("someone without the admin role is told so instead of seeing broken screens", async ({ page }) => {
    await signIn(page, "bob", "/audit");
    await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
    await expect(page.locator(".rail-identity")).toContainText("Not an administrator");
    await page.screenshot({ path: "test-results/no-access.png" });
  });
});
